"""APR-RT1 zero-production contract/source regression evidence.

Tests verify frozen architecture fences, NOT an implemented streaming runtime.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


CONTRACT = Path(
    "docs/agent_platform/APR_RT1_REALTIME_STREAMING_SESSION_CONTRACT_BDFA82F2.md"
)
INFERENCE = Path("se/src/runtimes/agent/contracts/inference.py")
ADAPTER = Path("se/src/runtimes/agent/adapters/inference.py")
PROVIDER = Path("se/src/provider/handlers/chat_handler.py")
CLIENT_WS = Path("cl/src/core/realtime_client.py")
AE_PERSISTENCE = Path("se/src/runtimes/agent/persistence.py")
AE_SCHEMA = Path("se/src/domain/schemas/agent_execution.py")
APR_X1 = Path(
    "docs/agent_platform/APR_X1_EXECUTION_LANE_EVENT_SEQUENCER_CONTRACT_C86BCC5E.md"
)
APR_FC1 = Path(
    "docs/agent_platform/APR_FC1_FAST_CONTROL_FRESHNESS_DEADLINE_CONTRACT_5FAEAE02.md"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return " ".join(
        text.replace("`", "")
        .replace("/**", "/__RT1_GLOBSTAR__")
        .replace("**", "")
        .replace("/__RT1_GLOBSTAR__", "/**")
        .split()
    )


def _contract() -> str:
    return _norm(_read(CONTRACT))


def _section(title: str) -> str:
    full = _read(CONTRACT)
    marker = "## " + title + "\n"
    assert marker in full, f"missing contract heading: {title}"
    content = full.split(marker, 1)[1].split("\n## ", 1)[0]
    return _norm(content)


def _require(text: str, *clauses: str) -> None:
    for clause in clauses:
        assert clause in text, f"missing APR-RT1 contract clause: {clause}"


def _class(path: Path, name: str) -> ast.ClassDef:
    for item in ast.parse(_read(path)).body:
        if isinstance(item, ast.ClassDef) and item.name == name:
            return item
    raise AssertionError(f"{name} missing from {path}")


def test_rt1_exact_two_new_path_contract_claim_and_production_hold() -> None:
    s = _section("1. Frozen scope and authority")
    _require(
        s,
        "stage = APR-RT1",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "independent PRE-CLAIM = PASS / RELEASED",
        "contract CLAIM = ACTIVE",
        "main@edd8e053d8233039b301a67f133a4a6006fe4f6d",
        "exact changed paths = 2 NEW / 2",
        "third path = PROHIBITED",
        "se/src/** delta = ZERO",
        "cl/** delta = ZERO",
        "production PRE-CLAIM = HOLD / NOT RELEASED",
        "production CLAIM = NONE",
        "merge authority = NONE",
        CONTRACT.name,
        Path(__file__).name,
        "A third path requires an independent PRE-CLAIM amendment",
    )


def test_inference_port_is_currently_non_streaming_and_no_method_is_added() -> None:
    port = _class(INFERENCE, "InferencePort")
    methods = {
        item.name
        for item in port.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"complete"}
    assert "Execute one non-streaming inference turn." in _read(INFERENCE)
    assert "execute_with_fallback" in _read(ADAPTER)
    assert "streaming_mode" in _read(PROVIDER)
    _require(
        _section("2. Source-grounded baseline and capability boundaries"),
        "one non-streaming inference turn",
        "InferencePort.stream()",
        "not an Agent Live audio/duplex session",
        "REALTIME != FAST_CONTROL",
        "does not grant more Tools",
    )


def test_session_and_reconnect_generation_never_mint_agent_identity() -> None:
    s = _section("3. Independent identities and transport generation")
    _require(
        s,
        "owner_user_id",
        "agent_instance_id",
        "execution_id",
        "runtime_session_id",
        "connection_id",
        "transport generation",
        "stream_request_id",
        "chunk_sequence",
        "AgentExecution.owner_instance_id",
        "not agent_instance_id",
        "A reconnect MUST NOT mint a new Agent identity",
        "foreign-owner, unknown session/generation or mismatched request is rejected",
    )
    schema = _class(AE_SCHEMA, "AgentExecution")
    fields = {
        item.target.id
        for item in schema.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
    }
    assert {"execution_id", "owner_instance_id", "revision"} <= fields


def test_conceptual_stream_records_and_capability_negotiation_fail_closed() -> None:
    s = _section("4. Conceptual provider-neutral InferenceStream")
    _require(
        s,
        "conceptual contract records",
        "StreamOpen(",
        "StreamInput(",
        "StreamChunk(",
        "StreamTerminal(",
        "StreamCancel(",
        "TEXT token streaming",
        "AUDIO_INPUT",
        "AUDIO_OUTPUT",
        "DUPLEX audio",
        "Unsupported modalities MUST fail closed",
        "monotonically increasing per-request chunk_sequence",
        "stable terminal identity",
        "no chunk is a final inference response",
        "StreamTerminal carries final/refusal/length/error/usage semantics",
        "After the first visible chunk, fallback MUST NOT replay published content",
    )


def test_duplex_barge_in_and_cancel_order_stop_new_side_effects() -> None:
    s = _section("5. Duplex input, barge-in and deterministic preemption")
    _require(
        s,
        "P0 trusted security revocation/emergency stop",
        "P1 AE committed cancel/terminal state",
        "P2 validated live user barge-in/interrupt",
        "P3 caller deadline, TBO horizon",
        "P4 eligible next stream chunk/input/Tool progress event",
        "not merely muting playback",
        "Stop new emission and new Tool side effects",
        "AE-R6 reconciliation",
        "never bypass AE durability",
    )


def test_partials_do_not_mutate_ae_and_replay_is_not_exactly_once_delivery() -> None:
    s = _section("6. APR-X1 sequencer and AE durable adoption")
    _require(
        s,
        "APR-X1 observes before adopting",
        "DecisionCommit",
        "not independent persistence authority",
        "Only AE expected-revision/CAS can commit durable execution state",
        "StreamChunk",
        "MUST NOT directly mutate AE state, CTX Memory, authorization",
        "If AE CAS loses, adoption fails closed",
        "Exactly-once adoption is not exactly-once network delivery",
        "AgentEventEnvelope.event_id",
    )
    _require(_norm(_read(APR_X1)), "DecisionCommit != independent persistence authority")
    persistence = _read(AE_PERSISTENCE)
    assert "expected_revision" in persistence
    assert "ExecutionConflictError" in persistence


def test_backpressure_and_connection_generation_are_bounded() -> None:
    s = _section("7. Bounded buffering and reconnect semantics")
    _require(
        s,
        "bounded buffer and backpressure",
        "unbounded queues are prohibited",
        "silently dropping required cancellation, refusal, usage or terminal signals is prohibited",
        "new transport generation",
        "fresh authentication/authorization",
        "No replay of Tool side effects",
        "Missing gap/ack state must fail closed",
        "does not delete CTX Memory",
    )
    client = _read(CLIENT_WS)
    assert "Exactly one receiver thread owns websocket.recv()" in client
    assert "self.connection_id" in client
    assert "self._receiver_thread" in client


def test_retry_budget_deadline_and_quota_do_not_reset_on_reconnect() -> None:
    s = _section("8. Deadline, provider fallback, UBQ and TBO fences")
    _require(
        s,
        "InferenceRequest.deadline_monotonic",
        "process-local clock domain",
        "MUST NOT reset the caller's absolute logical deadline",
        "UBQ owner",
        "TBO owns task horizon",
        "MUST NOT double-charge or lose ambiguous usage",
        "Cancellation and disconnection do not waive usage already incurred",
        "no new UBQ debit ledger",
    )
    assert "deadline_monotonic" in _read(ADAPTER)
    assert "quota_context" in _read(ADAPTER)


def test_json_schema_terminal_validation_is_atomic_and_client_owns_live_ux() -> None:
    s = _section("9. SO-C0, schema validation and client/live UX")
    _require(
        s,
        "SO-C0 JSON_SCHEMA terminal validation is atomic",
        "provisional chunks are not accepted as a validated final structured object",
        "not converted into Tool arguments before final owner validation",
        "CL-UI #242 owns Live transcript presentation",
        "client connection/transport owner retains authenticated WebSocket lifecycle",
        "No invented deployed voice stack",
    )


def test_realtime_not_fast_control_and_no_cross_owner_production_release() -> None:
    _require(
        _section("2. Source-grounded baseline and capability boundaries"),
        "REALTIME != FAST_CONTROL",
        "activating either profile does not grant more Tools",
    )
    _require(_norm(_read(APR_FC1)), "FAST_CONTROL")
    s = _section("10. Explicit cross-owner dependency exit matrix")
    _require(
        s,
        "Provider streams/fallback/structured output",
        "AE durability and APR-X1 sequencing",
        "UBQ usage, retries and TBO task horizon",
        "Client duplex transport and Live UI",
        "Agent identity, Memory",
        "Agent capability/runtime/sandbox",
        "FAST_CONTROL",
        "fresh exact-main production PRE-CLAIM",
        "no production scope",
    )


def test_contract_only_evidence_final_and_wave_gates_remain_closed() -> None:
    s = _section("11. Evidence and exit gates")
    _require(
        s,
        "must not modify them",
        "current Agent InferencePort.complete() remains non-streaming",
        "distinct",
        "post-visible-fallback fence",
        "duplex barge-in/cancellation",
        "AE expected-revision/CAS",
        "UBQ/TBO unchanged deadlines",
        "exact-head Linux and Windows Architecture GREEN = REQUIRED",
        "independent contract FINAL PASS = REQUIRED",
        "independent FINAL = PENDING",
        "READY / FROZEN = NO",
        "merge authority = NONE",
        "production PRE-CLAIM = HOLD / NOT RELEASED",
        "post-merge exact-new-main",
    )


def _conceptual_stream_fields() -> dict[str, tuple[str, ...]]:
    """Parse conceptual contract signatures; never instantiate production DTOs."""
    source = _read(CONTRACT)
    records = re.findall(
        r"(?m)^(Stream(?:Open|Input|Chunk|Terminal|Cancel))\(([\s\S]*?)\)",
        source,
    )
    return {
        name: tuple(piece.strip() for piece in arguments.split(","))
        for name, arguments in records
    }


def test_duplex_stream_input_requires_actual_bounded_content_or_asset_ref() -> None:
    records = _conceptual_stream_fields()
    assert set(records) == {
        "StreamOpen", "StreamInput", "StreamChunk", "StreamTerminal",
        "StreamCancel",
    }
    assert "payload_type" in records["StreamInput"]
    assert "inline_payload_or_immutable_payload_ref" in records["StreamInput"]
    s = _section("4. Conceptual provider-neutral InferenceStream")
    _require(
        s,
        "exactly one",
        "bounded inline payload",
        "scoped immutable payload reference",
        "content identity/hash",
        "media type, TTL and access authorization",
        "payload_type alone is insufficient",
        "No unspecified out-of-band input channel is allowed",
        "Absent, expired, mutable, oversized, foreign or mismatched input content MUST fail closed",
        "does not implement a new blob store",
    )


def test_all_stream_projections_bind_owner_session_execution_and_generation() -> None:
    records = _conceptual_stream_fields()
    required = {
        "owner_user_id", "execution_id", "runtime_session_id", "connection_id",
        "generation", "stream_request_id",
    }
    for record_name in (
        "StreamOpen", "StreamInput", "StreamChunk", "StreamTerminal",
        "StreamCancel",
    ):
        assert required <= set(records[record_name]), (
            f"{record_name} missing mandatory provenance "
            f"{sorted(required - set(records[record_name]))}"
        )
    assert {"agent_instance_id", "correlation_id"} <= set(records["StreamOpen"])
    assert "terminal_id" in records["StreamTerminal"]
    assert "chunk_sequence" in records["StreamChunk"]
    assert {"input_sequence", "event_id"} <= set(records["StreamInput"])
    s = _section("4. Conceptual provider-neutral InferenceStream")
    _require(
        s,
        "mandatory immutable provenance fields",
        "immutable stream-request-to-provenance binding",
        "lifetime of the active request",
        "bounded replay/terminal-retention horizon",
        "currently authorized transport generation",
        "A late terminal carrying an old generation",
        "MUST be rejected",
        "On reconnect, a new transport generation",
        "never silently inherit an old generation",
    )
