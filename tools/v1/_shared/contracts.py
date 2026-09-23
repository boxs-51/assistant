from __future__ import annotations

from copy import deepcopy
from typing import Any, TypeAlias, TypedDict

from .errors import ToolContractError, validate_error_code
from .validation import assert_json_safe, require_non_empty_string

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

TOOL_RESULT_REQUIRED_KEYS = ("ok", "tool", "action", "data", "error", "meta")
_META_RESERVED_KEYS = frozenset({"version", "truncated", "warnings"})


class ToolErrorPayload(TypedDict):
    code: str
    message: str
    retryable: bool
    details: dict[str, JSONValue]


class ToolResultMeta(TypedDict):
    version: str
    truncated: bool
    warnings: list[str]


class ToolResult(TypedDict):
    ok: bool
    tool: str
    action: str
    data: JSONValue
    error: ToolErrorPayload | None
    meta: ToolResultMeta


def _build_meta(
    *,
    version: str,
    truncated: bool,
    warnings: list[str] | tuple[str, ...] | None,
    extra_meta: dict[str, JSONValue] | None,
) -> dict[str, JSONValue]:
    require_non_empty_string(version, name="version")
    if type(truncated) is not bool:
        raise ToolContractError("truncated must be a boolean")

    warning_list: list[str] = []
    for index, warning in enumerate(warnings or []):
        if not isinstance(warning, str) or not warning:
            raise ToolContractError(f"warnings[{index}] must be a non-empty string")
        warning_list.append(warning)

    extras = deepcopy(extra_meta or {})
    assert_json_safe(extras, path="$.meta")
    collision = _META_RESERVED_KEYS.intersection(extras)
    if collision:
        names = ", ".join(sorted(collision))
        raise ToolContractError(f"extra_meta cannot override reserved keys: {names}")

    meta: dict[str, JSONValue] = {
        "version": version,
        "truncated": truncated,
        "warnings": warning_list,
    }
    meta.update(extras)
    return meta


def success_result(
    *,
    tool: str,
    action: str,
    version: str,
    data: JSONValue = None,
    truncated: bool = False,
    warnings: list[str] | tuple[str, ...] | None = None,
    extra_meta: dict[str, JSONValue] | None = None,
) -> ToolResult:
    require_non_empty_string(tool, name="tool")
    require_non_empty_string(action, name="action")
    copied_data = deepcopy(data)
    assert_json_safe(copied_data, path="$.data")

    result: ToolResult = {
        "ok": True,
        "tool": tool,
        "action": action,
        "data": copied_data,
        "error": None,
        "meta": _build_meta(
            version=version,
            truncated=truncated,
            warnings=warnings,
            extra_meta=extra_meta,
        ),
    }
    assert_json_safe(result)
    return result


def failure_result(
    *,
    tool: str,
    action: str,
    version: str,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, JSONValue] | None = None,
    truncated: bool = False,
    warnings: list[str] | tuple[str, ...] | None = None,
    extra_meta: dict[str, JSONValue] | None = None,
) -> ToolResult:
    require_non_empty_string(tool, name="tool")
    require_non_empty_string(action, name="action")
    validate_error_code(code)
    require_non_empty_string(message, name="message")
    if type(retryable) is not bool:
        raise ToolContractError("retryable must be a boolean")

    copied_details = deepcopy(details or {})
    assert_json_safe(copied_details, path="$.error.details")

    result: ToolResult = {
        "ok": False,
        "tool": tool,
        "action": action,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": copied_details,
        },
        "meta": _build_meta(
            version=version,
            truncated=truncated,
            warnings=warnings,
            extra_meta=extra_meta,
        ),
    }
    assert_json_safe(result)
    return result


def tool_result_schema(data_schema: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical terminal ToolResult JSON Schema around *data_schema*."""
    if not isinstance(data_schema, dict):
        raise ToolContractError("data_schema must be a JSON Schema mapping")
    copied_data_schema = deepcopy(data_schema)
    assert_json_safe(copied_data_schema, path="$.data_schema")

    meta_schema = {
        "type": "object",
        "properties": {
            "version": {"type": "string", "minLength": 1},
            "truncated": {"type": "boolean"},
            "warnings": {"type": "array", "items": {"type": "string", "minLength": 1}},
        },
        "required": ["version", "truncated", "warnings"],
        "additionalProperties": True,
    }
    error_schema = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
            "message": {"type": "string", "minLength": 1},
            "retryable": {"type": "boolean"},
            "details": {"type": "object"},
        },
        "required": ["code", "message", "retryable", "details"],
        "additionalProperties": False,
    }
    common_properties = {
        "tool": {"type": "string", "minLength": 1},
        "action": {"type": "string", "minLength": 1},
        "meta": meta_schema,
    }

    return {
        "type": "object",
        "required": list(TOOL_RESULT_REQUIRED_KEYS),
        "properties": {
            "ok": {"type": "boolean"},
            "tool": common_properties["tool"],
            "action": common_properties["action"],
            "data": {},
            "error": {},
            "meta": common_properties["meta"],
        },
        "additionalProperties": False,
        "oneOf": [
            {
                "properties": {
                    "ok": {"const": True},
                    "tool": common_properties["tool"],
                    "action": common_properties["action"],
                    "data": copied_data_schema,
                    "error": {"type": "null"},
                    "meta": common_properties["meta"],
                }
            },
            {
                "properties": {
                    "ok": {"const": False},
                    "tool": common_properties["tool"],
                    "action": common_properties["action"],
                    "data": {"type": "null"},
                    "error": error_schema,
                    "meta": common_properties["meta"],
                }
            },
        ],
    }
