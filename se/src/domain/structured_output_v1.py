"""Dormant, pure STRUCTURED_OUTPUT_V1 parsing and portable-schema core.

SO-P1A / Issue #410: this module is deliberately NOT wired into the gateway,
providers, Agent execution, checkpoints, streaming or durable admission.
It DOES NOT implement RFC 8785/JCS, a schema hash, or the canonical 32-KiB
schema-envelope bound. Those require separately authorized SO-P1B work.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

REVISION = "STRUCTURED_OUTPUT_V1"
MAX_OUTPUT_BYTES = 128 * 1024
# Conservative *provisional raw-input* ceiling; NOT JCS canonical size proof.
MAX_PROVISIONAL_SCHEMA_BYTES = 8 * 1024
MAX_OUTPUT_DEPTH = 32
MAX_SCHEMA_DEPTH = 8
MAX_JSON_NODES = 16_384
MAX_SCHEMA_PROPERTIES = 128
MAX_ENUM_VALUES = 64
MAX_NUMBER_TOKEN_CHARS = 100
MAX_NUMBER_ADJUSTED_EXPONENT = 1_000
_ALLOWED_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
_ALLOWED_KEYWORDS = frozenset(
    {"type", "properties", "required", "additionalProperties", "items",
     "description", "enum"}
)


class StructuredOutputError(ValueError):
    """Fail-closed, non-sensitive error domain (no input echoed)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise StructuredOutputError(code)


def _valid_unicode(value: str, code: str) -> None:
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        _fail(code)


def _preflight_depth(text: str, *, limit: int, code: str) -> None:
    # Bounded, linear lexical guard before the stdlib decoder can recurse.
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                _fail(code)
        elif char in "]}":
            depth -= 1
            if depth < 0:
                _fail(code)


def _decode(raw: bytes, *, code: str, max_bytes: int, max_depth: int) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > max_bytes:
        _fail(code)
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail(code)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        _fail(code)
    _preflight_depth(text, limit=max_depth, code=code)

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            # json decodes escaped JSON property names BEFORE this hook.
            _valid_unicode(key, code)
            if key in result:
                _fail(code)
            result[key] = value
        return result

    def _integer(token: str) -> int:
        if len(token) > MAX_NUMBER_TOKEN_CHARS:
            _fail(code)
        try:
            return int(token)
        except (ValueError, OverflowError):
            _fail(code)

    def _decimal(token: str) -> Decimal:
        if len(token) > MAX_NUMBER_TOKEN_CHARS:
            _fail(code)
        try:
            value = Decimal(token)
            if not value.is_finite() or abs(value.adjusted()) > MAX_NUMBER_ADJUSTED_EXPONENT:
                _fail(code)
            return value
        except (InvalidOperation, ValueError, OverflowError):
            _fail(code)

    def _constant(_token: str) -> NoReturn:
        _fail(code)

    try:
        value = json.loads(
            text, object_pairs_hook=_pairs, parse_int=_integer,
            parse_float=_decimal, parse_constant=_constant,
        )
    except (ValueError, TypeError, OverflowError, RecursionError):
        _fail(code)
    if type(value) is not dict:
        _fail(code)

    # Include both keys and values, and verify escaped surrogate codepoints.
    nodes = 0
    pending: list[tuple[Any, int]] = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > max_depth:
            _fail(code)
        if type(current) is dict:
            for key, item in current.items():
                _valid_unicode(key, code)
                pending.append((item, depth + 1))
        elif type(current) is list:
            pending.extend((item, depth + 1) for item in current)
        elif type(current) is str:
            _valid_unicode(current, code)
        elif type(current) is Decimal and not current.is_finite():
            _fail(code)
    return value


def parse_json_object(raw: bytes) -> dict[str, Any]:
    """Strictly parse original UTF-8 output bytes, never a pre-parsed dict."""
    return _decode(
        raw, code="OUTPUT_JSON_INVALID",
        max_bytes=MAX_OUTPUT_BYTES, max_depth=MAX_OUTPUT_DEPTH,
    )


def _matches_type(value: Any, kind: str) -> bool:
    # bool subclasses int in Python; NEVER coerce booleans to numbers.
    if kind == "object":
        return type(value) is dict
    if kind == "array":
        return type(value) is list
    if kind == "string":
        return type(value) is str
    if kind == "integer":
        # V1 conservative lexical integer test: 1.0 is a number, not an int.
        return type(value) is int or (type(value) is Decimal and value == value.to_integral_value())
    if kind == "number":
        return type(value) in (int, Decimal)
    if kind == "boolean":
        return type(value) is bool
    if kind == "null":
        return value is None
    return False


def _enum_equal(left: Any, right: Any) -> bool:
    if type(left) in (int, Decimal) and type(right) in (int, Decimal):
        return left == right
    return type(left) is type(right) and left == right


def _validate_schema_node(node: Any, *, depth: int, property_count: list[int]) -> None:
    code = "OUTPUT_SCHEMA_INVALID"
    if type(node) is not dict or depth > MAX_SCHEMA_DEPTH:
        _fail(code)
    if not set(node).issubset(_ALLOWED_KEYWORDS):
        _fail(code)
    kind = node.get("type")
    if type(kind) is not str or kind not in _ALLOWED_TYPES:
        _fail(code)
    if "description" in node and type(node["description"]) is not str:
        _fail(code)

    if kind == "object":
        if not {"properties", "required", "additionalProperties"}.issubset(node):
            _fail(code)
        if "items" in node or "enum" in node or node["additionalProperties"] is not False:
            _fail(code)
        properties = node["properties"]
        required = node["required"]
        if type(properties) is not dict or type(required) is not list:
            _fail(code)
        if any(type(name) is not str for name in properties):
            _fail(code)
        if any(type(name) is not str for name in required) or len(set(required)) != len(required):
            _fail(code)
        if set(properties) != set(required):
            _fail(code)
        property_count[0] += len(properties)
        if property_count[0] > MAX_SCHEMA_PROPERTIES:
            _fail(code)
        for sub in properties.values():
            _validate_schema_node(sub, depth=depth + 1, property_count=property_count)
    elif kind == "array":
        if "items" not in node or any(
            keyword in node for keyword in ("properties", "required", "additionalProperties", "enum")
        ):
            _fail(code)
        _validate_schema_node(node["items"], depth=depth + 1, property_count=property_count)
    else:
        if any(keyword in node for keyword in
               ("properties", "required", "additionalProperties", "items")):
            _fail(code)
        if "enum" in node:
            choices = node["enum"]
            if type(choices) is not list or not choices or len(choices) > MAX_ENUM_VALUES:
                _fail(code)
            if any(not _matches_type(item, kind) for item in choices):
                _fail(code)
            for index, item in enumerate(choices):
                if any(_enum_equal(item, prev) for prev in choices[:index]):
                    _fail(code)


def validate_portable_schema(raw_schema: bytes) -> dict[str, Any]:
    """Validate raw schema JSON with a *provisional* 8-KiB cap.

    This is NOT normative 32-KiB RFC 8785/JCS full-envelope admission;
    no schema hash, profile identity, or persistence is produced.
    """
    node = _decode(
        raw_schema, code="OUTPUT_SCHEMA_INVALID",
        max_bytes=MAX_PROVISIONAL_SCHEMA_BYTES, max_depth=MAX_OUTPUT_DEPTH,
    )
    if node.get("type") != "object":
        _fail("OUTPUT_SCHEMA_INVALID")
    _validate_schema_node(node, depth=1, property_count=[0])
    return node


def _check_instance(value: Any, schema: dict[str, Any]) -> None:
    kind: str = schema["type"]
    if not _matches_type(value, kind):
        _fail("OUTPUT_SCHEMA_MISMATCH")
    if kind == "object":
        props = schema["properties"]
        if value.keys() != props.keys():
            _fail("OUTPUT_SCHEMA_MISMATCH")
        for key, child_schema in props.items():
            _check_instance(value[key], child_schema)
    elif kind == "array":
        for child in value:
            _check_instance(child, schema["items"])
    elif "enum" in schema and not any(
        _enum_equal(value, option) for option in schema["enum"]
    ):
        _fail("OUTPUT_SCHEMA_MISMATCH")


def validate_json_schema_result(raw_output: bytes, raw_schema: bytes) -> dict[str, Any]:
    """Validate an isolated JSON_SCHEMA result; no live runtime side effects."""
    schema = validate_portable_schema(raw_schema)
    value = parse_json_object(raw_output)
    _check_instance(value, schema)
    return value
