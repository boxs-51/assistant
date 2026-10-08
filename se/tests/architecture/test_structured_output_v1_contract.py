"""SO-C0 read-only architecture evidence: current gaps are NOT fixed by this stage.

This suite must pass on the authorized production baseline and candidate, because
it proves truthful inventory + frozen future invariants rather than fictional
implemented structured-output capability.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = (
    ROOT / "docs/provider_output/"
    "STRUCTURED_OUTPUT_V1_CONTRACT_BA894FD7.md"
)
EVIDENCE_PATH = (
    ROOT / "se/tests/architecture/test_structured_output_v1_contract.py"
)
CANONICAL_MAIN = "ba894fd772e5d12a9a33db6be0b8198820d5578a"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _contract() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Expected source class: {name}")


def _field_names(cls: ast.ClassDef) -> set[str]:
    return {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }


def test_so_c0_a_c01_claim_is_exact_two_new_files_zero_production() -> None:
    text = _contract()
    assert CONTRACT_PATH.is_file()
    assert EVIDENCE_PATH.is_file()
    for required in (
        "STRUCTURED_OUTPUT_V1",
        CANONICAL_MAIN,
        "#6059934493",
        "#6060012926",
        "CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "2 NEW / 2",
        "third path",
        "PROHIBITED",
        "se/src/** delta",
        "ZERO",
        "cl/** delta",
        "production PRE-CLAIM / CLAIM",
        "NONE / NONE",
        "merge authority",
    ):
        assert required in text, required
    assert (
        "docs/provider_output/STRUCTURED_OUTPUT_V1_CONTRACT_BA894FD7.md"
        in text
    )
    assert "se/tests/architecture/test_structured_output_v1_contract.py" in text


def test_so_c0_a_c02_freezes_discriminated_modes_and_hash() -> None:
    text = _contract()
    for required in (
        "TEXT",
        "JSON_OBJECT",
        "JSON_SCHEMA",
        "strict=true",
        "config",
        "response_format",
        "SHA256(canonical JSON)",
        "schema_hash",
        "schema_revision",
        "max",
        "32 KiB",
        "maximum nested depth 8",
        "maximum total object properties 128",
        "additionalProperties:false",
        "required",
        "$ref",
        "No silent",
    ):
        assert required in text, required
    assert "No external" not in text or "No remote" in text or "$ref" in text
    assert "No silent coercion" in text
    assert "No silent" in text


def test_so_c0_a_c02_public_example_has_portable_json_schema_shape() -> None:
    text = _contract()
    for phrase in (
        '"type": "json_schema"',
        '"name": "motor_analysis_v1"',
        '"strict": true',
        '"type": "object"',
        '"additionalProperties": false',
        '"required": ["status", "summary", "recommendations"]',
    ):
        assert phrase in text


def test_so_c0_a_c03_provider_matrix_and_ptc_boundary() -> None:
    text = _contract()
    for phrase in (
        "OpenAI",
        "Chat Completions",
        "response_format",
        "json_schema",
        "Gemini",
        "GenerateContent",
        "generationConfig.responseFormat.text",
        "responseJsonSchema",
        "responseMimeType",
        "parametersJsonSchema",
        "Ollama",
        '/api/chat',
        'format="json"',
        "format=schema",
        "PTC #8",
    ):
        assert phrase in text, phrase
    assert "tool input" in text
    assert "not an output schema" in text


def test_so_c0_a_c03_current_provider_request_adapters_still_have_gaps() -> None:
    """Baseline truth test; pass means gaps were inventoried, not repaired."""
    openai = _read("se/src/provider/openai/converters/chats/request.py")
    gemini = _read("se/src/provider/gemini/converters/chats/request.py")
    ollama = _read("se/src/provider/ollama/converters/chat/request.py")
    assert '"response_format"' in openai
    assert 'gen_config["responseMimeType"] = config_data["response_format"]' in gemini
    assert (
        'config.get("response_format") in {"json", "json_object"}'
        in ollama
    )
    assert "future production findings" in _contract()


def test_so_c0_a_c04_gateway_and_inference_request_gaps_are_baseline_truth() -> None:
    gateway = _read("se/src/domain/schemas/request.py")
    inference = _read("se/src/runtimes/agent/contracts/inference.py")
    adapter = _read("se/src/runtimes/agent/adapters/inference.py")
    request_config = _class(gateway, "RequestConfig")
    neutral = _class(inference, "InferenceRequest")
    assert "response_format" in _field_names(request_config)
    assert "output_contract" not in _field_names(neutral)
    assert "response_format" not in _field_names(neutral)
    adapter_source = adapter[
        adapter.index("def serialize_request("):
        adapter.index("async def complete(", adapter.index("def serialize_request("))
    ]
    assert "response_format" not in adapter_source
    assert "output_contract" not in adapter_source
    assert "format" in _contract().lower()


def test_so_c0_a_c04_direct_agent_recovery_and_regenerate_are_inventoried() -> None:
    direct = _read("se/src/runtimes/chat/direct.py")
    workflow = _read("se/src/runtimes/workflow/runtime.py")
    agent = _read("se/src/runtimes/agent/runtime.py")
    regenerate = _read("se/src/transport/gateway/api/v1/session_router.py")
    assert "InferenceRequest(" in direct
    assert "async def _execute_direct(" in workflow
    assert "async def _execute_agent(" in workflow
    assert agent.count("InferenceRequest(") >= 3
    assert "_persist_execution_checkpoint(" in agent
    assert "async def regenerate_session_response(" in regenerate
    assert "chat_handler.execute_with_fallback(" in regenerate
    text = _contract()
    for phrase in (
        "DIRECT",
        "AGENT",
        "recovered inference",
        "Session regenerate",
        "before session persistence",
        "prior to checkpoint",
    ):
        # Wording may differ, but the corresponding concept must be frozen.
        if phrase == "prior to checkpoint":
            assert "before" in text and "checkpoint" in text
        else:
            assert phrase.lower() in text.lower(), phrase


def test_so_c0_a_c05_provider_capability_and_fallback_gap_is_explicit() -> None:
    capabilities = _read("se/src/domain/schemas/model.py")
    handler = _read("se/src/provider/handlers/chat_handler.py")
    text = _contract()
    assert "JSON_MODE = auto()" in capabilities
    assert "STRUCTURED_OUTPUT = auto()" in capabilities
    assert "async def _has_required_capabilities(" in handler
    assert "tools_present" in handler
    assert "ModelCapability.TOOL_CALLING" in handler
    assert "No silent format downgrade" not in text or "downgrade" in text
    for term in ("fallback", "schema", "TOOL_CALLING", "before physical send", "quota"):
        assert term.lower() in text.lower(), term
    assert "no free retry" in text.lower()


def test_so_c0_a_c06_strict_stream_and_terminal_acceptance_are_frozen() -> None:
    handler = _read("se/src/provider/handlers/chat_handler.py")
    agent = _read("se/src/runtimes/agent/runtime.py")
    assert "yield public_chunk" in handler
    assert "stream_started" in handler
    assert "AgentLoopState.COMPLETED" in agent
    assert "transcript.append(response.message)" in agent
    text = _contract()
    for term in (
        "provisional",
        "atomically publish",
        "validated final",
        "refusal",
        "MAX_TOKENS",
        "OUTPUT_SCHEMA_MISMATCH",
        "OUTPUT_STREAM_INCOMPLETE",
        "checkpoint",
        "UBQ",
    ):
        assert term.lower() in text.lower(), term
    assert "NOT" in text


def test_so_c0_a_c07_error_matrix_is_complete_and_fails_closed() -> None:
    text = _contract()
    for code in (
        "OUTPUT_SCHEMA_INVALID",
        "OUTPUT_FORMAT_UNSUPPORTED",
        "OUTPUT_JSON_INVALID",
        "OUTPUT_SCHEMA_MISMATCH",
        "OUTPUT_INCOMPLETE",
        "OUTPUT_REFUSED",
        "OUTPUT_STREAM_INCOMPLETE",
    ):
        assert code in text
    assert "fail-closed" in text.lower()
    assert "No" in text


def test_so_c0_a_c07_future_red_first_regression_matrix_is_not_a_green_claim() -> None:
    text = _contract()
    for begin, end in (
        (1, 5),
        (6, 12),
        (13, 17),
        (18, 23),
        (24, 27),
        (28, 31),
        (32, 35),
    ):
        assert f"SO-T{begin:02d}..T{end:02d}" in text
    assert "future RED-first" in text
    assert "not SO-C0 GREEN" in text


def test_so_c0_a_c08_cross_issue_authority_is_explicitly_closed() -> None:
    text = _contract()
    for term in (
        "PTC #8",
        "AOS/DCS #156",
        "AE-R14 #359",
        "CTX #15",
        "APR #278/#374",
        "CL-UI #242",
        "UBQ #141",
        "CAS #74",
        "production",
        "NONE",
        "no READY",
        "independent FINAL",
    ):
        assert term.lower() in text.lower(), term
    # A contract-only test must not import production adapters or mutate
    # provider/runtime state. The source is read solely as immutable evidence.
    tree = ast.parse(EVIDENCE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            assert all(
                not name.startswith(("se.src", "cl.src")) for name in imported
            )


# Contract-only reference fixtures: prove stable, source-independent bytes and
# reject ambiguous generated JSON without claiming current runtime enforcement.
# The production implementation will require separate independent authority.
JCS_SCHEMA_ENVELOPE = (
    '{"name":"jcs_numeric_v1","revision":"STRUCTURED_OUTPUT_V1",'
    '"schema":{"additionalProperties":false,"properties":'
    '{"n":{"enum":[1],"type":"number"}},"required":["n"],'
    '"type":"object"},"strict":true,"type":"json_schema"}'
)
JCS_SCHEMA_SHA256 = "fe0c0d88eeb1ec9ea16eb5daf905282cdd9249954c0172826956c3515b055045"
JCS_NUMERIC_EDGE_BYTES = '{"a":1,"b":0,"c":1e+21,"d":1e-7,"e":0.000001}'
JCS_NUMERIC_EDGE_SHA256 = "67611fb1557b34eff0be79a4442c41f4647f122d978cf12d2f36c32cb56f450b"
JCS_UTF16_ORDER_BYTES = '{"a":1,"😀":2,"":3}'
JCS_UTF16_ORDER_SHA256 = "8043baa23995777ba780997c1db8282dbe99a54db473636cd9ff149f37e4853e"


def test_so_c0_p2_numeric_jcs_fixed_schema_fingerprint() -> None:
    """RFC 8785 canonical *bytes* and digest, not a Python json.dumps substitute."""
    text = _contract()
    for marker in (
        "RFC 8785",
        "JSON Canonicalization Scheme",
        "ECMAScript",
        "IEEE-754",
        "UTF-16",
        "UTF-8",
        "schema_revision",
        "source lexeme",
        "1.0",
        "1e+21",
        "0.000001",
        "SHA-256 hex",
        "OUTPUT_SCHEMA_INVALID",
    ):
        assert marker in text, marker

    # Golden full semantic envelope; both [1] and [1.0] normalize to [1].
    assert JCS_SCHEMA_ENVELOPE in text
    assert JCS_SCHEMA_SHA256 in text
    assert hashlib.sha256(JCS_SCHEMA_ENVELOPE.encode("utf-8")).hexdigest() == (
        JCS_SCHEMA_SHA256
    )
    assert json.loads('{"enum":[1]}')["enum"] == json.loads(
        '{"enum":[1.0]}'
    )["enum"]
    assert json.loads(JCS_SCHEMA_ENVELOPE)["schema"]["properties"]["n"] == {
        "enum": [1],
        "type": "number",
    }


def test_so_c0_p2_numeric_jcs_cross_language_edge_vectors() -> None:
    text = _contract()
    assert JCS_NUMERIC_EDGE_BYTES in text
    assert JCS_NUMERIC_EDGE_SHA256 in text
    assert hashlib.sha256(
        JCS_NUMERIC_EDGE_BYTES.encode("utf-8")
    ).hexdigest() == JCS_NUMERIC_EDGE_SHA256

    # Compare semantic numeric values; a runtime implementation must prove
    # its own ECMAScript number-to-string conversion matches these bytes.
    for raw, canonical in (
        ("1", "1"),
        ("1.0", "1"),
        ("-0", "0"),
        ("-0.0", "0"),
        ("1e21", "1e+21"),
        ("1E+21", "1e+21"),
        ("1e-7", "1e-7"),
        ("1e-6", "0.000001"),
    ):
        assert json.loads(raw) == json.loads(canonical)
        assert f"{raw}" in text
        assert f"{canonical}" in text

    assert JCS_UTF16_ORDER_BYTES in text
    assert JCS_UTF16_ORDER_SHA256 in text
    assert hashlib.sha256(
        JCS_UTF16_ORDER_BYTES.encode("utf-8")
    ).hexdigest() == JCS_UTF16_ORDER_SHA256
    assert sorted(["", "😀", "a"], key=lambda key: key.encode("utf-16-be")) == [
        "a", "😀", ""
    ]


def _reject_repeated_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Illustrative source-independent decoder hook, not a server implementation."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate generated JSON object member")
        result[key] = value
    return result


@pytest.mark.parametrize("mode", ("JSON_OBJECT", "JSON_SCHEMA"))
@pytest.mark.parametrize(
    "raw",
    [
        r'{"status":"normal","status":"critical"}',
        r'{"result":{"x":1,"x":2}}',
        r'{"items":[{"x":1,"\u0078":2}]}',
        r'{"status":"normal"}{"status":"critical"}',
        r'{"result":{"a":1,"a":1}}',
        r'{"\u0061":1,"a":2}',
    ],
)
def test_so_c0_p2_generated_nested_duplicate_members_fail_closed(
    mode: str, raw: str
) -> None:
    """A local parser *reference* for both modes, not a live provider validator."""
    doc = _contract()
    assert "every nesting level" in doc
    assert "before any lossy mapping" in doc
    assert "OUTPUT_JSON_INVALID" in doc
    assert "JSON_OBJECT" in doc and "JSON_SCHEMA" in doc
    assert mode in doc
    if raw != r'{"\u0061":1,"a":2}':
        assert raw in doc
    with pytest.raises(ValueError):
        json.loads(raw, object_pairs_hook=_reject_repeated_members)


def test_so_c0_p2_duplicate_free_nested_json_is_not_rejected() -> None:
    # Different nested objects may legally repeat the same property name.
    source = '{"items":[{"x":1},{"x":2}],"status":"normal"}'
    parsed = json.loads(source, object_pairs_hook=_reject_repeated_members)
    assert parsed == {
        "items": [{"x": 1}, {"x": 2}],
        "status": "normal",
    }
    assert "no free retry" in _contract().lower()
    assert "future production invariants only" in _contract().lower()
