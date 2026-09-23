from __future__ import annotations

import importlib
import re
from copy import deepcopy
from typing import Any, Mapping, TypedDict

from .contracts import TOOL_RESULT_REQUIRED_KEYS
from .errors import ToolJsonSafetyError, ToolMetadataError
from .validation import assert_json_safe

_MANIFEST_VERSION = "2.0"
_ROOT_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_EXPORT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

_ALLOWED_KINDS = frozenset({"TOOL"})
_ALLOWED_EXECUTION_MODES = frozenset({"ONE_SHOT", "STREAMING", "LONG_RUNNING"})
_ALLOWED_IDEMPOTENCY = frozenset(
    {"IDEMPOTENT", "DEDUPLICATED", "NON_IDEMPOTENT", "UNKNOWN"}
)
_ALLOWED_EFFECTS = frozenset(
    {"READ", "WRITE", "EXECUTE", "EXTERNAL_SIDE_EFFECT", "PRIVILEGED"}
)
_ALLOWED_RISKS = frozenset({"LOW", "MEDIUM", "HIGH"})

_ROOT_REQUIRED = (
    "manifest_version",
    "name",
    "version",
    "description",
    "expose_root",
    "exports",
)
_EXPORT_REQUIRED = (
    "id",
    "version",
    "name",
    "description",
    "bind",
    "input_schema",
    "output_schema",
    "kind",
    "execution_mode",
    "idempotency",
    "effects",
    "base_risk",
    "required_scopes",
    "required_permissions",
    "danger_patterns",
)


class ToolCapabilityExport(TypedDict):
    id: str
    version: str
    name: str
    description: str
    bind: dict[str, Any]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    kind: str
    execution_mode: str
    idempotency: str
    effects: list[str]
    base_risk: str
    required_scopes: list[str]
    required_permissions: list[str]
    danger_patterns: list[str]


class ToolManifestV2(TypedDict):
    manifest_version: str
    name: str
    version: str
    description: str
    expose_root: bool
    exports: list[ToolCapabilityExport]


def _require_fields(mapping: Mapping[str, Any], names: tuple[str, ...], *, where: str) -> None:
    missing = [name for name in names if name not in mapping]
    if missing:
        raise ToolMetadataError(f"{where} missing required fields: {', '.join(missing)}")


def _require_non_empty_string(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolMetadataError(f"{where} must be a non-empty string")
    return value


def _require_unique_string_list(
    value: Any,
    *,
    where: str,
    allowed: frozenset[str] | None = None,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise ToolMetadataError(f"{where} must be a list")
    if not allow_empty and not value:
        raise ToolMetadataError(f"{where} must not be empty")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ToolMetadataError(f"{where}[{index}] must be a non-empty string")
        if item in seen:
            raise ToolMetadataError(f"{where} contains duplicate value {item!r}")
        if allowed is not None and item not in allowed:
            raise ToolMetadataError(f"{where} contains unsupported value {item!r}")
        seen.add(item)
        result.append(item)
    return result


def _load_jsonschema_components():
    try:
        validators = importlib.import_module("jsonschema.validators")
        exceptions = importlib.import_module("jsonschema.exceptions")
    except ImportError as exc:
        raise ToolMetadataError(
            "jsonschema is required for full Metadata V2 schema validation"
        ) from exc
    return validators, exceptions


def _validate_json_schema(schema: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise ToolMetadataError(f"{where} must be a JSON Schema object")
    try:
        assert_json_safe(schema, path=where)
    except ToolJsonSafetyError as exc:
        raise ToolMetadataError(str(exc)) from exc

    validators, exceptions = _load_jsonschema_components()
    try:
        validator_cls = validators.validator_for(schema)
        validator_cls.check_schema(schema)
    except exceptions.SchemaError as exc:
        raise ToolMetadataError(f"{where} is not a valid JSON Schema: {exc.message}") from exc
    return schema


def _validate_input_schema(schema: Any, *, where: str) -> dict[str, Any]:
    schema = _validate_json_schema(schema, where=where)
    if schema.get("type") != "object":
        raise ToolMetadataError(f"{where}.type must be 'object'")
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict):
        raise ToolMetadataError(f"{where}.properties must be an object")
    if not isinstance(required, list):
        raise ToolMetadataError(f"{where}.required must be a list")
    if schema.get("additionalProperties") is not False:
        raise ToolMetadataError(f"{where}.additionalProperties must be false")

    seen_required: set[str] = set()
    for index, key in enumerate(required):
        if not isinstance(key, str) or not key:
            raise ToolMetadataError(f"{where}.required[{index}] must be a non-empty string")
        if key in seen_required:
            raise ToolMetadataError(f"{where}.required contains duplicate key {key!r}")
        if key not in properties:
            raise ToolMetadataError(f"{where}.required key {key!r} is not in properties")
        seen_required.add(key)
    return schema


def _validate_output_schema(schema: Any, *, where: str) -> dict[str, Any]:
    schema = _validate_json_schema(schema, where=where)
    if schema.get("type") != "object":
        raise ToolMetadataError(f"{where}.type must be 'object'")
    required = schema.get("required")
    if not isinstance(required, list):
        raise ToolMetadataError(f"{where}.required must be a list")
    missing = [key for key in TOOL_RESULT_REQUIRED_KEYS if key not in required]
    if missing:
        raise ToolMetadataError(
            f"{where} must describe ToolResult required keys: {', '.join(missing)}"
        )
    return schema


def validate_tool_manifest_v2(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a defensive copy of one frozen Metadata V2 manifest."""
    if not isinstance(manifest, Mapping):
        raise ToolMetadataError("manifest must be a mapping")
    _require_fields(manifest, _ROOT_REQUIRED, where="manifest")

    if manifest["manifest_version"] != _MANIFEST_VERSION:
        raise ToolMetadataError("manifest.manifest_version must equal '2.0'")

    root_name = _require_non_empty_string(manifest["name"], where="manifest.name")
    if not _ROOT_NAME_RE.fullmatch(root_name):
        raise ToolMetadataError("manifest.name must be lowercase snake_case")
    _require_non_empty_string(manifest["version"], where="manifest.version")
    _require_non_empty_string(manifest["description"], where="manifest.description")
    if type(manifest["expose_root"]) is not bool:
        raise ToolMetadataError("manifest.expose_root must be a boolean")

    exports = manifest["exports"]
    if not isinstance(exports, list) or not exports:
        raise ToolMetadataError("manifest.exports must be a non-empty list")

    seen_ids: set[str] = set()
    for index, export in enumerate(exports):
        where = f"manifest.exports[{index}]"
        if not isinstance(export, Mapping):
            raise ToolMetadataError(f"{where} must be a mapping")
        _require_fields(export, _EXPORT_REQUIRED, where=where)

        export_id = _require_non_empty_string(export["id"], where=f"{where}.id")
        if not _EXPORT_ID_RE.fullmatch(export_id):
            raise ToolMetadataError(f"{where}.id must be a lowercase dotted capability ID")
        if export_id in seen_ids:
            raise ToolMetadataError(f"duplicate export id {export_id!r}")
        seen_ids.add(export_id)

        export_name = _require_non_empty_string(export["name"], where=f"{where}.name")
        if export_name != export_id:
            raise ToolMetadataError(f"{where}.name must equal export id")
        _require_non_empty_string(export["version"], where=f"{where}.version")
        _require_non_empty_string(export["description"], where=f"{where}.description")

        bind = export["bind"]
        if not isinstance(bind, Mapping):
            raise ToolMetadataError(f"{where}.bind must be a mapping")
        for key in bind:
            if not isinstance(key, str) or not key:
                raise ToolMetadataError(f"{where}.bind keys must be non-empty strings")
        try:
            assert_json_safe(bind, path=f"{where}.bind")
        except ToolJsonSafetyError as exc:
            raise ToolMetadataError(str(exc)) from exc

        input_schema = _validate_input_schema(export["input_schema"], where=f"{where}.input_schema")
        output_schema = _validate_output_schema(export["output_schema"], where=f"{where}.output_schema")
        del output_schema

        properties = input_schema["properties"]
        required = input_schema["required"]
        collisions = set(bind).intersection(properties)
        if collisions:
            raise ToolMetadataError(
                f"{where}.bind collides with public input properties: {', '.join(sorted(collisions))}"
            )
        required_collisions = set(bind).intersection(required)
        if required_collisions:
            raise ToolMetadataError(
                f"{where}.bind collides with public required keys: {', '.join(sorted(required_collisions))}"
            )

        if export["kind"] not in _ALLOWED_KINDS:
            raise ToolMetadataError(f"{where}.kind must be 'TOOL'")
        if export["execution_mode"] not in _ALLOWED_EXECUTION_MODES:
            raise ToolMetadataError(f"{where}.execution_mode is unsupported")
        if export["idempotency"] not in _ALLOWED_IDEMPOTENCY:
            raise ToolMetadataError(f"{where}.idempotency is unsupported")

        _require_unique_string_list(
            export["effects"],
            where=f"{where}.effects",
            allowed=_ALLOWED_EFFECTS,
            allow_empty=False,
        )
        if export["base_risk"] not in _ALLOWED_RISKS:
            raise ToolMetadataError(f"{where}.base_risk is unsupported")
        _require_unique_string_list(export["required_scopes"], where=f"{where}.required_scopes")
        _require_unique_string_list(
            export["required_permissions"], where=f"{where}.required_permissions"
        )
        danger_patterns = _require_unique_string_list(
            export["danger_patterns"], where=f"{where}.danger_patterns"
        )
        for pattern in danger_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ToolMetadataError(
                    f"{where}.danger_patterns contains invalid regex {pattern!r}: {exc}"
                ) from exc

    copied = deepcopy(dict(manifest))
    try:
        assert_json_safe(copied, path="manifest")
    except ToolJsonSafetyError as exc:
        raise ToolMetadataError(str(exc)) from exc
    return copied
