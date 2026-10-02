from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/CTX_F5_3I_B0_TOOL_RESPONSE_PAYLOAD_SOURCE_OWNER_HANDOFF.md"
)
TOOL_PAYLOAD = Path("se/src/context/tool_response_payload.py")
SOURCE_ADAPTERS = Path("se/src/context/source_adapters.py")
TOOL_RESULT_MODEL = Path(
    "se/src/infrastructure/storage/models/sql/agent/tool_result.py"
)
AGENT_PERSISTENCE = Path("se/src/runtimes/agent/persistence.py")
RESUME_PLANNING = Path("se/src/runtimes/agent/resume_planning.py")
MEMORY_PROMOTION = Path("se/src/context/memory_promotion.py")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _semantic(value: str) -> str:
    return " ".join(value.replace(chr(96), "").split())


def _check_constraint_literals(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name != "CheckConstraint" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            values.append(first.value)
    return tuple(values)


def test_b0_is_exact_two_file_zero_production_contract_slice() -> None:
    contract = _semantic(_read(CONTRACT))

    for phrase in (
        "class = CONTRACT / ARCHITECTURE EVIDENCE ONLY",
        "exact development baseline = eef643e703d89aebdb6a4ffb7d159b805e2f6685",
        "baseline Architecture #1772 = GREEN/GREEN",
        "selected source kind for handoff contract = TOOL_RESPONSE_PAYLOAD",
        "production source adapter = CLOSED",
        "production PRE-CLAIM = NONE",
        "B1 production PRE-CLAIM = CLOSED",
        "production/runtime/schema/migration delta = ZERO",
        "merge authority = NONE",
        "CTX-F5-3I-B0 is exactly two files",
        "No se/src/** production file changes",
    ):
        assert phrase in contract


def test_existing_ctx_f1_payload_identity_is_canonical_and_committed_only() -> None:
    payload = _read(TOOL_PAYLOAD)

    for phrase in (
        'TOOL_RESPONSE_PAYLOAD_IDENTITY_DOMAIN = "ctx-tool-response-payload-v1"',
        'COMMITTED_RESULT_STATE = "COMMITTED"',
        "def canonical_payload_bytes(",
        "def tool_response_content_digest(",
        "def tool_response_payload_id(",
        '"source_result_id": source_result_id',
        '"invocation_id": invocation_id',
        '"execution_id": execution_id',
        '"tool_call_id": tool_call_id',
        '"logical_capability_id": logical_capability_id',
        '"payload_schema_version": payload_schema_version',
        '"content_digest": content_digest',
        "if payload.source_commit_state != COMMITTED_RESULT_STATE:",
        "Only COMMITTED tool results may create ToolResponsePayload.",
    ):
        assert phrase in payload


def test_existing_projection_binds_payload_identity_but_metadata_is_only_hint() -> None:
    adapters = _read(SOURCE_ADAPTERS)
    contract = _semantic(_read(CONTRACT))

    for phrase in (
        "def project_tool_response_payload_source(",
        "validate_tool_response_payload_integrity(payload)",
        "if result.commit_state != COMMITTED_RESULT_STATE:",
        "authority_id=payload.payload_id",
        'metadata={"source_result_id": result.id}',
    ):
        assert phrase in adapters

    for phrase in (
        'source_ref.metadata["source_result_id"] is an untrusted lookup hint only',
        "metadata is not part of the canonical ContextSourceRef identity tuple",
        "derived payload_id == source_ref.authority_id",
        "The metadata hint is compared only after server-side reconstruction succeeds",
    ):
        assert phrase in contract


def test_agent_result_persists_retryable_independently_without_success_coupling() -> None:
    model = _read(TOOL_RESULT_MODEL)

    for phrase in (
        "class AgentToolResultRecord(Base):",
        "success: Mapped[bool] = mapped_column(Boolean",
        "error_code: Mapped[Optional[str]]",
        "error_message: Mapped[Optional[str]]",
        "retryable: Mapped[bool] = mapped_column(Boolean",
        "commit_state: Mapped[str] = mapped_column(",
    ):
        assert phrase in model

    constraints = _check_constraint_literals(model)
    assert "commit_state IN ('PROVISIONAL', 'COMMITTED')" in constraints
    assert all("success" not in item.lower() for item in constraints)
    assert all("retryable" not in item.lower() for item in constraints)


def test_r7_success_projection_sets_retryable_false_in_persistence_and_resume() -> None:
    persistence = _read(AGENT_PERSISTENCE)
    resume = _read(RESUME_PLANNING)

    for source in (persistence, resume):
        assert '"success": succeeded' in source
        assert '"error_code": None if succeeded else' in source
        assert '"error_message": None if succeeded else' in source
        assert '"retryable": False if succeeded else' in source

    assert '"commit_state": "COMMITTED"' in persistence


def test_v3_exact_success_eligibility_and_malformed_row_fail_closed_are_frozen() -> None:
    contract = _semantic(_read(CONTRACT))

    for phrase in (
        "result.commit_state == COMMITTED",
        "result.success is True",
        "result.error_code is None",
        "result.error_message is None",
        "result.retryable is False",
        "COMMITTED + success=True + retryable=True",
        "MALFORMED / NON-CANONICAL / INELIGIBLE",
        "mint no SourcePromotionProof",
        "issue no new promotion reservation",
        "perform no repair or normalization write",
        "do not reinterpret the row from mutable CapabilityInvocation state",
        "Failed COMMITTED results remain CLOSED",
    ):
        assert phrase in contract


def test_v1_lineage_owner_and_payload_reconstruction_remain_exact() -> None:
    contract = _semantic(_read(CONTRACT))

    for phrase in (
        "AgentToolResultRecord(result.id) -> AgentExecutionRecord(result.execution_id) -> canonical chat_data.Session(execution.session_id) -> CapabilityInvocationRecord(result.invocation_id) -> AgentToolCallRecord(execution_id, result.tool_call_id)",
        "Session.user_id == requested owner_user_id == source_ref.owner_user_id",
        "invocation.execution_id == result.execution_id",
        "invocation.session_id == execution.session_id",
        "invocation.owner_user_id == Session.user_id",
        "invocation.tool_call_id == result.tool_call_id",
        "invocation.capability_id == result.capability_id",
        "tool_call.iteration_id == result.iteration_id",
        "tool_call.invocation_id == result.invocation_id",
        "tool_call.capability_id == result.capability_id",
        "An AgentSessionRecord.owner_user_id is not a substitute for canonical Chat Session ownership",
        "authoritative source content is exactly the durable AgentToolResultRecord.output",
        "canonicalize that exact JSON using existing CTX-F1 canonical_payload_bytes(...) rules",
        "derived payload_id == source_ref.authority_id",
        "whole canonical JSON payload only",
    ):
        assert phrase in contract


def test_state_token_binds_v3_semantics_and_excludes_mutable_runtime_authority() -> None:
    contract = _semantic(_read(CONTRACT))

    for phrase in (
        "domain = ctx-trp-source-state-v1",
        "result.id",
        "result.execution_id",
        "result.iteration_id",
        "result.invocation_id",
        "result.tool_call_id",
        "result.capability_id",
        "commit_state = COMMITTED",
        "success = true",
        "error_code = null",
        "error_message = null",
        "retryable = false",
        "payload_schema_version",
        "canonical content_digest(result.output)",
        "canonical reconstructed payload_id",
        "owner_user_id",
        "session_id",
        "Omission of retryable=false is not permitted",
        "AgentExecution lease/state/revision",
        "CapabilityInvocation revision/state/attempt/outcome",
        "target/implementation/connection identity",
        "wall-clock freshness",
    ):
        assert phrase in contract


def test_proof_receipt_gc_and_opaque_json_boundaries_remain_closed() -> None:
    contract = _semantic(_read(CONTRACT))
    promotion = _read(MEMORY_PROMOTION)

    assert "class SourcePromotionProof(BaseModel):" in promotion
    assert "proof_receipt_id" in promotion
    assert "authority_state_token" in promotion

    for phrase in (
        "proof_receipt_id must be deterministic and stable",
        "same live immutable source yields the same proof_receipt_id / authority_state_token tuple",
        "one immutable TOOL_RESPONSE_PAYLOAD proof tuple may authorize at most one exact canonical MemoryPromotionIntent",
        "result missing / GC-collected",
        "no SourcePromotionProof",
        "no new durable reservation",
        "A CTX proof or reservation does not become an Agent live root",
        "later normal Agent/R11 GC does not retroactively mutate that durable reservation",
        "Embedded asset/file/provider-looking values are opaque JSON",
        "CAS validation/hydration/dereference/lifecycle authority = NONE",
        "FUTURE-FENCE-CAS-CTX-OPAQUE-JSON-PROJECTION-1",
    ):
        assert phrase in contract


def test_cross_track_and_future_production_authority_stays_closed() -> None:
    contract = _semantic(_read(CONTRACT))

    for phrase in (
        "re-admit or recharge UBQ quota",
        "reinterpret R6/R7 remote outcome or side-effect authority",
        "change R11/R12 retention, GC, lease, recovery, or continuation semantics",
        "create #156 capability target/routing/fingerprint/sandbox authority",
        "CAS / UBQ / R6 / R7 / R11 / R12 / Issue #156 authority transfer = NONE",
        "runtime/API/retrieval/ContextBuilder/model visibility = CLOSED",
        "B0 production source-adapter PRE-CLAIM = NONE",
        "B1 production PRE-CLAIM = CLOSED",
        "separately frozen B1 production PRE-CLAIM and independent release are required",
    ):
        assert phrase in contract
