from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


class ProviderToolContractError(ValueError):
    """Raised when provider-facing tool lowering cannot preserve identity safely."""


@dataclass(frozen=True, slots=True)
class ProviderToolNameRule:
    provider: str
    max_length: int | None
    safe_pattern: re.Pattern[str] | None
    alias_invalid_names: bool

    def is_safe(self, name: str) -> bool:
        if not isinstance(name, str) or not name:
            return False
        if self.max_length is not None and len(name) > self.max_length:
            return False
        if self.safe_pattern is None:
            return True
        return self.safe_pattern.fullmatch(name) is not None


_SAFE_FUNCTION_NAME = re.compile(r"[A-Za-z0-9_-]+")
_PROVIDER_RULES: dict[str, ProviderToolNameRule] = {
    # Conservative Chat Completions compatibility bound. PTC-2 owns the
    # provider-native envelope and request/response wiring.
    "openai": ProviderToolNameRule(
        provider="openai",
        max_length=64,
        safe_pattern=_SAFE_FUNCTION_NAME,
        alias_invalid_names=True,
    ),
    # Gemini GenerateContent documents [A-Za-z0-9_-] names up to 128 chars.
    "gemini": ProviderToolNameRule(
        provider="gemini",
        max_length=128,
        safe_pattern=_SAFE_FUNCTION_NAME,
        alias_invalid_names=True,
    ),
    # PTC-1 does not invent an Ollama naming restriction. PTC-2 still owns
    # Ollama ToolDefinition/history envelope conversion.
    "ollama": ProviderToolNameRule(
        provider="ollama",
        max_length=None,
        safe_pattern=None,
        alias_invalid_names=False,
    ),
}


def provider_tool_name_rule(provider: str) -> ProviderToolNameRule:
    key = str(provider).strip().lower()
    try:
        return _PROVIDER_RULES[key]
    except KeyError as exc:
        raise ProviderToolContractError(
            f"unsupported provider tool contract: {provider!r}"
        ) from exc


def _alias_for(logical_name: str, rule: ProviderToolNameRule) -> str:
    if not rule.alias_invalid_names:
        return logical_name

    digest = sha256(logical_name.encode("utf-8")).hexdigest()[:12]
    readable = re.sub(r"[^A-Za-z0-9_-]+", "_", logical_name).strip("_-")
    if not readable:
        readable = "tool"

    suffix = f"_{digest}"
    if rule.max_length is None:
        return f"{readable}{suffix}"

    prefix_budget = rule.max_length - len(suffix)
    if prefix_budget <= 0:
        raise ProviderToolContractError(
            f"provider {rule.provider!r} name limit is too small for collision-safe aliases"
        )

    alias = f"{readable[:prefix_budget]}{suffix}"
    if not rule.is_safe(alias):
        raise ProviderToolContractError(
            f"failed to construct a provider-safe alias for {logical_name!r}"
        )
    return alias


@dataclass(frozen=True, slots=True)
class ProviderToolNameMap:
    provider: str
    logical_to_provider: Mapping[str, str]
    provider_to_logical: Mapping[str, str]

    def provider_name(self, logical_name: str) -> str:
        try:
            return self.logical_to_provider[logical_name]
        except KeyError as exc:
            raise ProviderToolContractError(
                f"unknown logical tool name for {self.provider}: {logical_name!r}"
            ) from exc

    def logical_name(self, provider_name: str) -> str:
        try:
            return self.provider_to_logical[provider_name]
        except KeyError as exc:
            raise ProviderToolContractError(
                f"unknown provider tool name for {self.provider}: {provider_name!r}"
            ) from exc


def build_provider_tool_name_map(
    provider: str,
    logical_names: Iterable[str],
) -> ProviderToolNameMap:
    rule = provider_tool_name_rule(provider)
    forward: dict[str, str] = {}
    reverse: dict[str, str] = {}

    for logical_name in logical_names:
        if not isinstance(logical_name, str) or not logical_name:
            raise ProviderToolContractError("logical tool names must be non-empty strings")
        if logical_name in forward:
            raise ProviderToolContractError(
                f"duplicate logical tool name: {logical_name!r}"
            )

        provider_name = (
            logical_name if rule.is_safe(logical_name) else _alias_for(logical_name, rule)
        )
        previous = reverse.get(provider_name)
        if previous is not None and previous != logical_name:
            raise ProviderToolContractError(
                "provider tool alias collision: "
                f"{previous!r} and {logical_name!r} -> {provider_name!r}"
            )

        forward[logical_name] = provider_name
        reverse[provider_name] = logical_name

    return ProviderToolNameMap(
        provider=rule.provider,
        logical_to_provider=MappingProxyType(forward),
        provider_to_logical=MappingProxyType(reverse),
    )


_JSON_SCHEMA_TYPES = frozenset(
    {"null", "boolean", "object", "array", "number", "string", "integer"}
)
_SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
        # Draft-04/06/07 compatibility: each value may be either a schema
        # or a property-name array. The recursive helper leaves arrays intact.
        "dependencies",
    }
)
_SCHEMA_SINGLE_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "items",
        "unevaluatedItems",
        # Draft-04/06/07 tuple-schema compatibility.
        "additionalItems",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
    }
)
_SCHEMA_LIST_KEYWORDS = frozenset(
    {"prefixItems", "allOf", "anyOf", "oneOf"}
)


def _normalize_json_schema_type_tokens(schema: Any) -> Any:
    """Normalize legacy uppercase JSON-Schema type tokens on a provider copy.

    Older Gateway/provider-adapter fixtures used OpenAPI-style uppercase type
    values. Provider tool schemas are JSON Schema at the PTC boundary, so
    recognized primitive type tokens are canonicalized to lowercase recursively
    through schema-bearing keywords. Instance values under default/const/enum
    are intentionally not traversed.
    """

    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, Mapping):
        return schema

    normalized = dict(schema)
    schema_type = normalized.get("type")
    if isinstance(schema_type, str):
        lowered = schema_type.lower()
        if lowered in _JSON_SCHEMA_TYPES:
            normalized["type"] = lowered
    elif isinstance(schema_type, list):
        normalized["type"] = [
            (
                item.lower()
                if isinstance(item, str) and item.lower() in _JSON_SCHEMA_TYPES
                else item
            )
            for item in schema_type
        ]

    for keyword in _SCHEMA_MAP_KEYWORDS:
        child_map = normalized.get(keyword)
        if isinstance(child_map, Mapping):
            normalized[keyword] = {
                key: _normalize_json_schema_type_tokens(value)
                for key, value in child_map.items()
            }

    for keyword in _SCHEMA_SINGLE_KEYWORDS:
        child = normalized.get(keyword)
        if isinstance(child, (Mapping, bool)):
            normalized[keyword] = _normalize_json_schema_type_tokens(child)
        elif keyword == "items" and isinstance(child, list):
            normalized[keyword] = [
                _normalize_json_schema_type_tokens(value) for value in child
            ]

    for keyword in _SCHEMA_LIST_KEYWORDS:
        children = normalized.get(keyword)
        if isinstance(children, list):
            normalized[keyword] = [
                _normalize_json_schema_type_tokens(value) for value in children
            ]

    return normalized


def normalize_provider_tool_schema(
    provider: str,
    schema: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a provider transport copy without changing canonical authority.

    This helper deliberately performs only lossless transport normalization.
    Local/canonical JSON-Schema validation remains authoritative and PTC-1
    must not weaken semantic constraints to satisfy a provider.
    """

    provider_tool_name_rule(provider)
    if schema is None:
        return None
    if not isinstance(schema, Mapping):
        raise ProviderToolContractError("tool parameters must be a mapping or None")

    normalized = deepcopy(dict(schema))
    # $schema selects a meta-schema/dialect rather than constraining instances.
    normalized.pop("$schema", None)
    normalized = _normalize_json_schema_type_tokens(normalized)

    # Gateway tool invocations carry a JSON object of named arguments. Keep
    # that invariant identical across provider transports instead of allowing
    # provider-specific array/primitive roots to drift into the contract.
    root_type = normalized.get("type")
    if root_type is None:
        normalized["type"] = "object"
    elif root_type != "object":
        raise ProviderToolContractError(
            f"{provider} tool parameter JSON Schema root must be type 'object'"
        )
    normalized.setdefault("properties", {})
    return normalized


@dataclass(frozen=True, slots=True)
class LoweredProviderTool:
    logical_name: str
    provider_name: str
    description: str
    parameters: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ProviderToolContract:
    provider: str
    tools: tuple[LoweredProviderTool, ...]
    names: ProviderToolNameMap


def _tool_fields(tool: Any) -> tuple[str, str, Mapping[str, Any] | None]:
    if isinstance(tool, Mapping):
        name = tool.get("name")
        description = tool.get("description", "")
        parameters = tool.get("parameters")
    else:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", "")
        parameters = getattr(tool, "parameters", None)

    if not isinstance(name, str) or not name:
        raise ProviderToolContractError("tool definition must have a non-empty name")
    if not isinstance(description, str):
        raise ProviderToolContractError(f"tool {name!r} description must be a string")
    if parameters is not None and not isinstance(parameters, Mapping):
        raise ProviderToolContractError(f"tool {name!r} parameters must be a mapping")
    return name, description, parameters


def lower_provider_tools(
    provider: str,
    tools: Sequence[Any],
) -> ProviderToolContract:
    """Build the provider-neutral -> provider-facing intermediate contract.

    The result intentionally is not a provider-native request envelope.
    Adapter wiring and provider-native history conversion belong to PTC-2.
    This stage only freezes reversible names and schema copies.
    """

    extracted = [_tool_fields(tool) for tool in tools]
    names = build_provider_tool_name_map(provider, (item[0] for item in extracted))
    lowered = tuple(
        LoweredProviderTool(
            logical_name=name,
            provider_name=names.provider_name(name),
            description=description,
            parameters=normalize_provider_tool_schema(provider, parameters),
        )
        for name, description, parameters in extracted
    )
    return ProviderToolContract(provider=names.provider, tools=lowered, names=names)
