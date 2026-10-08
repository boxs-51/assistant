"""SO-P1A / #410: isolated RED-first and fail-closed pure-core evidence."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from se.src.domain.structured_output_v1 import (
    MAX_OUTPUT_BYTES,
    MAX_PROVISIONAL_SCHEMA_BYTES,
    StructuredOutputError,
    parse_json_object,
    validate_json_schema_result,
    validate_portable_schema,
)


def _schema(props: dict[str, object] | None = None) -> bytes:
    properties = props if props is not None else {
        "status": {"type": "string", "enum": ["normal", "warning", "critical"]},
        "count": {"type": "integer"},
        "details": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    }
    payload = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _assert_code(fn, code: str) -> None:
    with pytest.raises(StructuredOutputError) as caught:
        fn()
    assert caught.value.code == code
    # Error does not echo potentially sensitive raw output.
    assert str(caught.value) == code


def test_so_p1a_valid_strict_json_object_nested_utf8_and_numeric_tokens() -> None:
    value = parse_json_object('{"text":"Động cơ","items":[{"x":1.5}]}'.encode("utf-8"))
    assert value == {"text": "Động cơ", "items": [{"x": Decimal("1.5")}]}


@pytest.mark.parametrize("payload", [
    b'{"status":"normal","status":"critical"}',
    b'{"x":1,"x":1}',
    b'{"result":{"x":1,"x":2}}',
    b'{"items":[{"x":1,"\\u0078":2}]}',
    b'{"x":1,"\\u0078":1}',
    b'{"a":1}{"b":2}',
    b'{"a":1} trailing',
    b'{"a":NaN}',
    b'{"a":Infinity}',
    b'{"a":-Infinity}',
    b'{"a":1e10001}',
    b'{"a":00}',
    b'{"a":1} \x00',
    b'{"a":"\\ud800"}',
    b'{"\\udfff":"value"}',
    b'{"x":1,}',
    b'["not","an","object"]',
    b'"not an object"',
    b'null',
    b'',
    b'\xef\xbb\xbf{"a":1}',
    b'{"utf8":"\xff"}',
])
def test_so_p1a_invalid_raw_json_rejected(payload: bytes) -> None:
    _assert_code(lambda: parse_json_object(payload), "OUTPUT_JSON_INVALID")


def test_so_p1a_no_decoded_key_overwrite_and_valid_array() -> None:
    _assert_code(
        lambda: parse_json_object(b'{"items":[{"a":1,"\\u0061":2}]}'),
        "OUTPUT_JSON_INVALID",
    )
    assert parse_json_object(b'{"items":[{"a":1},{"a":2}]}')["items"] == [
        {"a": 1}, {"a": 2},
    ]


def test_so_p1a_non_bytes_and_object_size_bounds() -> None:
    for value in ('{"a":1}', {"a": 1}, bytearray(b'{"a":1}')):
        _assert_code(lambda v=value: parse_json_object(v), "OUTPUT_JSON_INVALID")
    _assert_code(
        lambda: parse_json_object(b'{"v":"' + b"a" * MAX_OUTPUT_BYTES + b'"}'),
        "OUTPUT_JSON_INVALID",
    )


def test_so_p1a_json_depth_and_complexity_are_bounded() -> None:
    _assert_code(
        lambda: parse_json_object(b'{"v":' + b"[" * 40 + b"0" + b"]" * 40 + b"}"),
        "OUTPUT_JSON_INVALID",
    )
    # This is below 128 KiB but above the node budget.
    _assert_code(
        lambda: parse_json_object(b'{"v":[' + b"0," * 17000 + b"0]}"),
        "OUTPUT_JSON_INVALID",
    )


def test_so_p1a_wide_object_counts_keys_and_values_in_node_budget() -> None:
    from se.src.domain.structured_output_v1 import MAX_JSON_NODES

    # Root contributes one node, and each member contributes its key + value.
    def wide_object(members: int) -> bytes:
        return b"{" + b",".join(
            b'"k' + str(index).encode("ascii") + b'":0'
            for index in range(members)
        ) + b"}"

    largest_valid = (MAX_JSON_NODES - 1) // 2
    accepted = wide_object(largest_valid)
    rejected = wide_object(largest_valid + 1)
    assert len(rejected) < MAX_OUTPUT_BYTES
    assert len(parse_json_object(accepted)) == largest_valid
    _assert_code(
        lambda: parse_json_object(rejected),
        "OUTPUT_JSON_INVALID",
    )


def test_so_p1a_portable_schema_positive_closed_nested() -> None:
    schema = validate_portable_schema(_schema())
    assert schema["type"] == "object"
    response = b'{"status":"normal","count":3,"details":{"ok":true}}'
    result = validate_json_schema_result(response, _schema())
    assert result["count"] == 3


@pytest.mark.parametrize("raw", [
    b'{"type":"object","properties":{},"required":[]}',  # missing closed
    b'{"type":"object","properties":{},"required":[],"additionalProperties":true}',
    b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"$ref":"x"}',
    b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"anyOf":[]}',
    b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"type":"object"}',
    b'{"type":"array","items":{"type":"string"},"additionalProperties":false}',
    b'{"type":"array"}',
    b'{"type":["string","null"]}',
    b'{"type":"object","properties":{"x":{"type":"string"}},"required":[],"additionalProperties":false}',
    b'{"type":"object","properties":{},"required":["unknown"],"additionalProperties":false}',
    b'{"type":"object","properties":{},"required":["x","x"],"additionalProperties":false}',
    b'{"type":"object","properties":{"x":{"type":"string"}},"required":["x"],"additionalProperties":false,"description":12}',
    b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"\\u0074ype":"object"}',
    b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"$defs":{}}',
    b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"default":{}}',
    b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"patternProperties":{}}',
])
def test_so_p1a_invalid_schema_fails_closed(raw: bytes) -> None:
    _assert_code(lambda: validate_portable_schema(raw), "OUTPUT_SCHEMA_INVALID")



@pytest.mark.parametrize("raw", [
    b'{"type":"string"}',
    b'{"type":"integer"}',
    b'{"type":"array","items":{"type":"string"}}',
])
def test_so_p1a_schema_root_must_be_closed_object(raw: bytes) -> None:
    _assert_code(lambda: validate_portable_schema(raw), "OUTPUT_SCHEMA_INVALID")
    _assert_code(
        lambda: validate_json_schema_result(b'{"a":1}', raw),
        "OUTPUT_SCHEMA_INVALID",
    )



@pytest.mark.parametrize("member_schema", [
    {"type": "string", "enum": [1]},
    {"type": "integer", "enum": [True]},
    {"type": "integer", "enum": [1, 1]},
    {"type": "string", "enum": []},
    {"type": "number", "enum": [1, 1.0]},
])
def test_so_p1a_nested_invalid_enum_is_checked_beyond_root_gate(
    member_schema: dict[str, object],
) -> None:
    # Valid closed object root forces actual enum validation.
    _assert_code(
        lambda: validate_portable_schema(_schema({"value": member_schema})),
        "OUTPUT_SCHEMA_INVALID",
    )


def test_so_p1a_schema_input_duplicate_key_provenance() -> None:
    # Raw input is mandatory; a dict already lost original duplicate members.
    _assert_code(
        lambda: validate_portable_schema(
            b'{"type":"object","\\u0074ype":"object",'
            b'"properties":{},"required":[],"additionalProperties":false}'
        ), "OUTPUT_SCHEMA_INVALID",
    )
    _assert_code(
        lambda: validate_portable_schema({"type": "object"}),
        "OUTPUT_SCHEMA_INVALID",
    )


def test_so_p1a_schema_depth_property_and_enum_limits() -> None:
    schema = {"type": "string"}
    for _ in range(10):
        schema = {"type": "array", "items": schema}
    _assert_code(
        lambda: validate_portable_schema(_schema({"deep": schema})),
        "OUTPUT_SCHEMA_INVALID",
    )
    props = {f"p{i}": {"type": "boolean"} for i in range(129)}
    _assert_code(lambda: validate_portable_schema(_schema(props)), "OUTPUT_SCHEMA_INVALID")
    _assert_code(
        lambda: validate_portable_schema(
            _schema({"value": {"type": "string", "enum": [str(i) for i in range(65)]}})
        ), "OUTPUT_SCHEMA_INVALID",
    )
    _assert_code(
        lambda: validate_portable_schema(
            b'{"type":"object","properties":{},"required":[],"additionalProperties":false,"description":"'
            + b"x" * MAX_PROVISIONAL_SCHEMA_BYTES + b'"}'
        ), "OUTPUT_SCHEMA_INVALID",
    )


@pytest.mark.parametrize("response", [
    b'{"status":"normal","count":"3","details":{"ok":true}}',
    b'{"status":"normal","count":true,"details":{"ok":true}}',
    b'{"status":"normal","count":3,"details":{"ok":"true"}}',
    b'{"status":"normal","count":3,"details":{"ok":true},"extra":1}',
    b'{"status":"normal","count":3,"details":{}}',
    b'{"status":"normal","count":3}',
    b'{"status":"invalid","count":3,"details":{"ok":true}}',
])
def test_so_p1a_schema_mismatch_never_coerces(response: bytes) -> None:
    _assert_code(
        lambda: validate_json_schema_result(response, _schema()),
        "OUTPUT_SCHEMA_MISMATCH",
    )



def test_so_p1a_json_schema_integral_decimal_is_integer_without_coercion() -> None:
    result = validate_json_schema_result(
        b'{"status":"normal","count":3.0,"details":{"ok":true}}', _schema()
    )
    assert result["count"] == Decimal("3.0")
    _assert_code(
        lambda: validate_json_schema_result(
            b'{"status":"normal","count":3.5,"details":{"ok":true}}', _schema()
        ), "OUTPUT_SCHEMA_MISMATCH",
    )


def test_so_p1a_schema_allows_eight_logical_nodes_but_not_nine() -> None:
    def root(array_layers: int) -> bytes:
        child: dict[str, object] = {"type": "string"}
        for _ in range(array_layers):
            child = {"type": "array", "items": child}
        closed_root = {
            "type": "object",
            "properties": {"item": child},
            "required": ["item"],
            "additionalProperties": False,
        }
        return json.dumps(closed_root).encode()

    # Root object (depth 1), six array nodes (2..7), string node (8).
    assert validate_portable_schema(root(6))["type"] == "object"
    # Root + seven array nodes + string node = 9: fail the *depth* gate.
    _assert_code(lambda: validate_portable_schema(root(7)), "OUTPUT_SCHEMA_INVALID")


def test_so_p1a_generated_duplicate_is_json_error_not_schema_mismatch() -> None:
    _assert_code(
        lambda: validate_json_schema_result(
            b'{"status":"normal","status":"normal"}', _schema()
        ), "OUTPUT_JSON_INVALID",
    )


def test_so_p1a_schema_invalid_precedes_generated_validation() -> None:
    _assert_code(
        lambda: validate_json_schema_result(
            b'{"x":1}',
            b'{"type":"object","properties":{},"required":[],"additionalProperties":true}',
        ), "OUTPUT_SCHEMA_INVALID",
    )


def test_so_p1a_type_enums_are_not_boolean_integer_aliases() -> None:
    schema = _schema({"n": {"type": "number", "enum": [1]}})
    assert validate_json_schema_result(b'{"n":1.0}', schema) == {"n": Decimal("1.0")}
    _assert_code(
        lambda: validate_json_schema_result(b'{"n":true}', schema),
        "OUTPUT_SCHEMA_MISMATCH",
    )


def test_so_p1a_pure_core_does_not_claim_jcs_or_live_integration() -> None:
    import inspect
    import se.src.domain.structured_output_v1 as core

    text = inspect.getsource(core)
    assert "NO" in text and "JCS" in text
    assert "schema_hash =" not in text
    assert "json.dumps(sort_keys=True)" not in text
    for forbidden in ("httpx", "requests.post", "AgentRuntime", "ProviderInferenceAdapter"):
        assert forbidden not in text
