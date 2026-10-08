from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/agent_platform/"
    "APR_X1_EXECUTION_LANE_EVENT_SEQUENCER_CONTRACT_C86BCC5E.md"
)
EXECUTION_SCHEMA = Path("se/src/domain/schemas/agent_execution.py")
STATE_MACHINE = Path("se/src/runtimes/agent/state_machine.py")
PERSISTENCE = Path("se/src/runtimes/agent/persistence.py")
RUNTIME = Path("se/src/runtimes/agent/runtime.py")
EVENTS = Path("se/src/runtimes/agent/contracts/events.py")
R14_A = Path(
    "docs/agent_execution_r14/"
    "R14_A_HEAD_AUDIT_FAULT_MATRIX_CONTRACT_FREEZE_10071F4E.md"
)
GAC_ACTION = Path("cl/src/game_automation/actions/action.py")
SERVER_PRODUCTION_ROOT = Path("se/src")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(path: Path) -> str:
    return " ".join(_read(path).replace("`", "").split())


def _class(source: str, name: str) -> ast.ClassDef:
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _annotated_names(cls: ast.ClassDef) -> set[str]:
    return {
        node.target.id
        for node in cls.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
    }


def _server_production_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SERVER_PRODUCTION_ROOT.rglob("*.py"))
    )


def test_apr_x1_exact_zero_production_claim_is_frozen() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "stage = APR-X1",
        "class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION",
        "contract CLAIM = ACTIVE",
        "production PRE-CLAIM = HOLD / NOT RELEASED",
        "production CLAIM = NONE",
        "AE revision/state/checkpoint authority = NONE",
        "fault/HITL authority = NONE",
        "SBX authority = NONE",
        "GAC authority = NONE",
        "merge authority = NONE",
        "exact changed paths = 2 NEW / 2",
        "third path = PROHIBITED",
        "se/src/** delta = ZERO",
        "cl/** delta = ZERO",
    ):
        assert phrase in contract

    assert "APR_X1_EXECUTION_LANE_EVENT_SEQUENCER_CONTRACT_C86BCC5E.md" in contract
    assert "test_apr_x1_execution_lane_event_sequencer_contract.py" in contract


def test_apr_x1_preserves_ae_revision_and_state_machine_authority() -> None:
    execution_source = _read(EXECUTION_SCHEMA)
    execution = _class(execution_source, "AgentExecution")
    assert "revision" in _annotated_names(execution)

    state_machine_source = _read(STATE_MACHINE)
    assert "class AgentExecutionStateMachine" in state_machine_source
    assert "can_transition" in state_machine_source
    assert "Invalid agent execution transition" in state_machine_source

    contract = _normalized(CONTRACT)
    for phrase in (
        "sequencer-local order != AgentExecution.revision",
        "DecisionCommit != independent persistence authority",
        "ExecutionLaneEvent != independent checkpoint authority",
        "expected-revision/CAS",
        "APR-X1 does not invent a replacement revision",
    ):
        assert phrase in contract


def test_apr_x1_preserves_expected_revision_cas_persistence() -> None:
    persistence = _read(PERSISTENCE)

    assert "expected_revision" in persistence
    assert "ExecutionConflictError" in persistence
    assert "Stale AgentExecution revision" in persistence

    contract = _normalized(CONTRACT)
    assert "A future implementation must commit durable mutation through AE-owned expected-revision/CAS" in contract
    assert "If CAS loses" in contract
    assert "no force-write occurs" in contract


def test_apr_x1_observes_existing_bounded_parallel_tool_work() -> None:
    schema = _read(EXECUTION_SCHEMA)
    runtime = _read(RUNTIME)

    limits = _class(schema, "AgentExecutionLimits")
    fields = _annotated_names(limits)
    assert "max_parallel_tools" in fields
    assert "max_parallel_agents" in fields

    assert "max_parallel=context.limits.max_parallel_tools" in runtime

    contract = _normalized(CONTRACT)
    assert "Multiple authorized actions may run concurrently" in contract
    assert "Their completions become observations" in contract
    assert "The sequencer serializes adoption/dispatch authority" in contract


def test_apr_x1_keeps_public_agent_events_projection_only() -> None:
    events_source = _read(EVENTS)
    envelope = _class(events_source, "AgentEventEnvelope")
    fields = _annotated_names(envelope)

    for field in ("event_id", "event_name", "timestamp", "correlation", "payload"):
        assert field in fields

    assert "revision" not in fields
    assert "sequence" not in fields

    contract = _normalized(CONTRACT)
    for phrase in (
        "public AgentEventEnvelope.event_id != AgentExecution.revision",
        "Current AgentEventEnvelope remains publication/projection telemetry",
        "MUST NOT become durable AgentExecution ordering authority",
    ):
        assert phrase in contract


def test_apr_x1_has_no_server_production_lane_types_yet() -> None:
    server_source = _server_production_source()

    for type_name in (
        "ExecutionEventSequencer",
        "DecisionCommit",
        "AgentObservation",
        "ResponseEmission",
    ):
        assert f"class {type_name}" not in server_source

    contract = _normalized(CONTRACT)
    for type_name in (
        "AgentObservation",
        "DecisionCommit",
        "ActionIntent",
        "ResponseEmission",
        "ExecutionLaneEvent",
        "ExecutionEventSequencer",
    ):
        assert type_name in contract


def test_apr_x1_freezes_observation_before_adoption_and_stale_rejection() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Every asynchronous completion becomes an observation before adoption",
        "source_execution_revision",
        "A physically completed result may still be stale",
        "adoption fails closed",
        "Late results cannot overwrite newer durable execution state",
        "Canonical state adoption must remain deterministically serialized",
    ):
        assert phrase in contract



def test_apr_x1_freezes_stable_same_revision_observation_order() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "For multiple eligible observations produced from the same source_execution_revision",
        "the sequencer MUST derive a stable adoption candidate order from immutable observation metadata",
        "source_execution_revision, semantic_priority, source_kind, source_identity, completion_or_result_identity",
        "MUST NOT depend on wall-clock completion time, coroutine scheduling, publisher delivery order, or process-local insertion order",
        "callback order MUST NOT be used as a fallback",
        "This ordering only ranks candidates for serialized adoption",
        "does not override AE state/revision/CAS authority",
    ):
        assert phrase in contract


def test_apr_x1_response_emission_has_no_durable_state_authority() -> None:
    contract = _normalized(CONTRACT)

    prohibited_block = (
        "Partial/streamed output MUST NOT by itself: "
        "- advance AgentExecution.revision; "
        "- change execution state; "
        "- mutate checkpoint truth; "
        "- commit CTX Memory; "
        "- authorize Tool/Skill/capability use; "
        "- finalize a side effect; "
        "- create retry/recovery truth; "
        "- establish AgentInstance identity."
    )

    assert "ResponseEmission is presentation/output progress only" in contract
    assert prohibited_block in contract


def test_apr_x1_preserves_restart_multi_worker_and_r14_boundaries() -> None:
    contract = _normalized(CONTRACT)
    r14 = _normalized(R14_A)

    for phrase in (
        "durable AE execution state/revision is canonical",
        "durable checkpoint/pending invocation state is canonical",
        "multi-worker one-winner behavior remains AE revision/CAS/lease/recovery-owned",
        "APR-X1 MUST NOT persist a second checkpoint log",
        "Issue #359 remains the AE-R14 evidence/fault owner",
        "Semantic overlap = MATERIAL BOUNDARY / FENCED",
        "R14-B/C are parallel non-canonical evidence candidates until separately landed",
    ):
        assert phrase in contract

    assert "R14-GAP-REMOTE-PRE-SIDE-EFFECT-1" in r14
    assert "R14-GAP-HITL-E2E-1" in r14
    assert "Fault-hook authority" in r14
    assert "zero R14 fault-hook transfer" in r14


def test_apr_x1_preserves_sbx_runtime_ownership_hold() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Issue #163 / PR #364 owns a separate production slice",
        "se/src/runtimes/agent/runtime.py",
        "Any future APR-X1 production PRE-CLAIM including AgentRuntime is HOLD",
        "APR-X1 receives zero sandbox authority",
    ):
        assert phrase in contract


def test_apr_x1_keeps_gac_action_intent_namespace_disjoint() -> None:
    gac_source = _read(GAC_ACTION)
    assert "class ActionIntent" in gac_source

    contract = _normalized(CONTRACT)
    for phrase in (
        "GAC #221 already owns client-local",
        "cl/src/game_automation/actions/action.py::ActionIntent",
        "APR-X1 ActionIntent is conceptual only",
        "Future server production naming must be namespace-disjoint",
    ):
        assert phrase in contract


def test_apr_x1_production_gate_and_final_evidence_remain_closed() -> None:
    contract = _normalized(CONTRACT)

    for phrase in (
        "Production gate remains HOLD",
        "current AE-R14 disposition",
        "SBX-2 ownership if AgentRuntime is targeted",
        "process-local vs durable lane-event design",
        "R6 invocation idempotency/reconciliation compatibility",
        "GAC ActionIntent namespace separation",
        "exact-head Linux + Windows Architecture GREEN",
        "independent APR-X1 contract FINAL PASS",
        "unresolved threads = 0",
        "P0/P1/P2 = 0/0/0",
    ):
        assert phrase in contract
