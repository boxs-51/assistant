from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/context_future/"
    "CTX_F5_3I_B8_P2_TRUSTED_PROMOTION_CALLER_AUTHORITY_FREEZE_89B248A9.md"
)
MANAGER = Path("se/src/infrastructure/storage/core/manager.py")
IDENTITY = Path("se/src/domain/schemas/identity.py")
SOURCE_ROOT = Path("se/src")
P3_CALLER = Path("se/src/transport/gateway/api/v1/session_router.py")
METHOD = "promote_tool_response_payload_memory"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).split())


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _function(cls: ast.ClassDef, name: str):
    for node in cls.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"function {name} not found")


def test_p2_freezes_exact_released_zero_production_claim() -> None:
    contract = _read(CONTRACT)

    for phrase in (
        "stage = CTX-F5-3I-B8-P2",
        "release audit baseline = 89b248a9e29e09313ba7344bb6ba737249b2ec0f",
        "development baseline = 4e30b39df362a67c0e95b410b44ae5dec1bc842c",
        "development baseline Architecture #2168 / 37334618013 = GREEN/GREEN",
        "parent CTX-F5-3I-B8 = LANDED / CANONICAL / HEALTHY",
        "parent CTX-F5-3I-B8-P1 = LANDED / CANONICAL / HEALTHY",
        "class = CONTRACT / ARCHITECTURE EVIDENCE / ZERO-PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = CLOSED",
        "production CLAIM = NONE",
        "automatic promotion = CLOSED",
        "F6 Personalization = CLOSED",
        "merge authority = NONE",
        "se/src/** delta = ZERO",
        "cl/** delta = ZERO",
        "config delta = ZERO",
        "schema/migration delta = ZERO",
        "runtime/API/container delta = ZERO",
    ):
        assert phrase in contract

    assert (
        "docs/context_future/"
        "CTX_F5_3I_B8_P2_TRUSTED_PROMOTION_CALLER_AUTHORITY_FREEZE_89B248A9.md"
        in contract
    )
    assert (
        "se/tests/architecture/"
        "test_ctx_f5_3i_b8_p2_trusted_promotion_caller_authority_freeze.py"
        in contract
    )


def test_p2_zero_caller_freeze_is_superseded_only_by_released_p3_caller() -> None:
    call_token = ".promote_tool_response_payload_memory("
    matches: list[str] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        if call_token in _read(path):
            matches.append(path.as_posix())

    assert matches == [P3_CALLER.as_posix()]

    caller_source = _read(P3_CALLER)
    caller = next(
        node
        for node in ast.parse(caller_source).body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "promote_tool_response_payload_memory_for_session"
    )
    caller_segment = ast.get_source_segment(caller_source, caller)
    assert caller_segment is not None
    assert caller_segment.count(call_token) == 1

    manager_source = _read(MANAGER)
    storage = _class(manager_source, "StorageEngine")
    handoff = _function(storage, METHOD)
    assert isinstance(handoff, ast.AsyncFunctionDef)

    handoff_source = ast.get_source_segment(manager_source, handoff)
    assert handoff_source is not None
    assert (
        handoff_source.count(
            "self.get_tool_response_payload_memory_promotion()"
        )
        == 1
    )
    assert handoff_source.count("await service.promote(") == 1

def test_p2_keeps_stage_markers_out_of_production_source() -> None:
    matches: list[str] = []

    for path in SOURCE_ROOT.rglob("*.py"):
        source = _read(path)
        if "CTX-F5-3I-B8-P2" in source or "CTX_F5_3I_B8_P2" in source:
            matches.append(path.as_posix())

    assert matches == []


def test_p2_binds_future_owner_authority_to_authenticated_identity() -> None:
    identity_source = _read(IDENTITY)
    identity = _class(identity_source, "Identity")
    annotated_names = {
        node.target.id
        for node in identity.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }
    assert "user_id" in annotated_names

    contract = _normalized(CONTRACT)
    for phrase in (
        "canonical `Identity.user_id`",
        "then-current authenticated server request/runtime authority",
        "`source_ref.owner_user_id`",
        "MUST NOT independently mint promotion owner authority",
        "client/model-supplied owner id",
        "runtime-session identity",
        "`AgentExecution.owner_instance_id`",
        "future `agent_instance_id`",
        "MUST NOT treat `source_ref.owner_user_id` alone as authentication authority",
        "B1/B5 source authority must independently re-prove source ownership",
    ):
        assert phrase in contract


def test_p2_requires_fresh_first_caller_production_preclaim() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "First real caller requires a separate production PRE-CLAIM",
        "exact caller function and owning class/module",
        "exact production path maximum",
        "exact invocation reason and entry condition",
        "exact authenticated owner provenance",
        "exact `ContextSourceRef` provenance",
        "then-current UBQ accounting implications",
        "then-current TBO/timeout implications",
        "Issue #156 capability/routing implications",
        "separate production CLAIM",
        "P2 itself releases no production path",
    ):
        assert phrase in contract


def test_p2_keeps_implicit_and_automatic_promotion_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Automatic promotion remains CLOSED",
        "tool completion",
        "capability completion",
        "AgentToolResult COMMITTED observation",
        "provider completion",
        "retry, recovery, reconciliation, or resume",
        "startup or shutdown",
        "Session, Task, Branch, or AgentExecution lifecycle",
        "event bus publication/subscription",
        "ContextBuilder",
        "Working Set",
        "ContextSnapshot",
        "detached/background worker",
        "timer or periodic scan",
        "No event, persistence transition, or durable source observation",
    ):
        assert phrase in contract


def test_p2_keeps_public_model_capability_and_budget_authority_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "HTTP/FastAPI endpoint",
        "WebSocket command",
        "DirectChatRuntime caller",
        "AgentRuntime caller",
        "Tool registration",
        "capability registration",
        "model-visible promotion operation",
        "`capability_id`",
        "DCS or Issue #156 routing authority",
        "fresh bilateral audit with Issue #156",
        "TaskBudget admission",
        "UBQ admission, charge, refund, reservation, or quota authority",
        "timeout/retry budget",
        "inference quota semantics",
        "tool-call quota semantics",
    ):
        assert phrase in contract


def test_p2_preserves_external_authority_and_later_ctx_fences() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "CAS #74 retains ASSET/media/provider/ObjectStorage/lifecycle/deletion/GC authority",
        "Issue #31/R11 retains transcript/checkpoint read-liveness, retention and destructive-GC authority",
        "Issue #107/R12 is closed/canonical",
        "does not grant CTX `main.py` authority",
        "APR #278 AgentInstance work remains separate",
        "Issue #156 retains Agent-only/capability-selection/routing/sandbox authority",
        "F6 Personalization remains CLOSED",
        "F7 pins/score/dedupe remains CLOSED",
        "F9 Working Set/ContextSnapshot remains CLOSED",
        "F10 CompactContext remains CLOSED",
    ):
        assert phrase in contract


def test_p2_exit_gate_requires_exact_head_ci_and_independent_final() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "P2 exit gate",
        "exact changed paths remain 2 / 2",
        "production/runtime/schema/migration/config/client delta remains zero",
        "architecture evidence proves production caller count is still zero",
        "exact-head Linux Architecture is GREEN",
        "exact-head Windows Architecture is GREEN",
        "independent P2 contract FINAL is PASS",
        "no unresolved blocking review thread exists",
        "no P0/P1 blocker exists",
        "no MATERIAL drift invalidates this freeze",
        "standing conditional auto-merge exception",
    ):
        assert phrase in contract
