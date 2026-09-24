import inspect
from pathlib import Path

from se.src.infrastructure.storage.models.sql.agent.checkpoint import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
)
from se.src.infrastructure.storage.models.sql.agent.execution import AgentExecutionRecord
from se.src.infrastructure.storage.models.sql.capability.invocation import CapabilityInvocationRecord
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs/agent_execution_r11/R11_F1_GC_ROOT_CLOSURE_DRY_RUN_CONTRACT_DB33F64D.md"
F0_CONTRACT = ROOT / "docs/agent_execution_r11/R11_F0_RETENTION_GC_LIVE_ROOT_CONTRACT_DF0F2E7F.md"


def _contract() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _f0_contract() -> str:
    return F0_CONTRACT.read_text(encoding="utf-8")


def _foreign_key_targets(column) -> set[str]:
    return {fk.target_fullname for fk in column.foreign_keys}


def test_f1_contract_is_pinned_to_exact_f0_parent_and_non_destructive():
    text = _contract()
    assert "db33f64d25a98709ff487f4dcba3281074f62f0c" in text
    assert "NO PRODUCTION DELETE" in text
    assert "classification from deletion" in text
    assert "no repository DELETE API" in text


def test_f1_freezes_required_semantic_edges_not_covered_by_agent_execution_fks():
    text = _contract()
    for field in (
        "parent_execution_id",
        "retry_of_execution_id",
        "base_execution_id",
        "base_checkpoint_id",
    ):
        column = AgentExecutionRecord.__table__.c[field]
        assert not _foreign_key_targets(column)
        assert f"AgentExecution.{field}" in text

    execution_id = CapabilityInvocationRecord.__table__.c.execution_id
    assert not _foreign_key_targets(execution_id)
    assert "CapabilityInvocation.execution_id -> AgentExecution.id" in text


def test_f1_freezes_deterministic_cycle_safe_closure_and_dry_run_reasons():
    text = _contract()
    required = (
        "stable root-family order",
        "stable identity ordering",
        "visited set",
        "RETAIN | CANDIDATE | FAIL_CLOSED",
        "INCOMPLETE_OR_INCONSISTENT_GRAPH",
        "byte-for-byte equivalent ordered classifications",
    )
    for phrase in required:
        assert phrase in text


def test_f1_preserves_checkpoint_pending_invocation_f0_graph_parity():
    text = _contract()
    f0_text = _f0_contract()

    checkpoint_id = AgentCheckpointPendingInvocationRecord.__table__.c.checkpoint_id
    foreign_keys = tuple(checkpoint_id.foreign_keys)
    assert len(foreign_keys) == 1
    assert (
        foreign_keys[0].target_fullname
        == f"{AgentExecutionCheckpointRecord.__tablename__}.checkpoint_id"
    )
    assert foreign_keys[0].ondelete == "CASCADE"

    assert "checkpoint pending invocation rows attached to a live checkpoint" in f0_text
    assert "checkpoint pending-invocation ownership" in f0_text
    assert "checkpoint -> pending invocation CASCADE" in f0_text

    required = (
        "checkpoint pending-invocation watermark authority",
        "AgentCheckpointPendingInvocationRecord",
        "agent_checkpoint_pending_invocations",
        "pending-invocation watermark rows attached to any retained checkpoint",
        "physical ownership only and never collection/liveness authority",
        "F0->F1 graph parity",
    )
    for phrase in required:
        assert phrase in text


def test_f1_pending_invocation_authority_is_tied_to_real_resume_consumer():
    text = _contract()
    source = inspect.getsource(AgentResumePlanningService.build_resume_plan)

    assert "load_checkpoint_pending_invocations" in source
    assert "CHECKPOINT_PENDING_INVOCATIONS_MISSING" in source
    assert "AgentResumePlanningService.build_resume_plan" in text
    assert "load_checkpoint_pending_invocations(checkpoint_id)" in text
    assert "CHECKPOINT_PENDING_INVOCATIONS_MISSING" in text


def test_f1_preserves_active_replay_side_effect_and_transcript_authority():
    text = _contract()
    required = (
        "WAITING/RUNNING/replayable executions",
        "ResumeClaim",
        "TaskBudget reservations",
        "COMMITTED tool-result",
        "CapabilityInvocation",
        "parent/retry/base execution/checkpoint lineage",
        "Shared transcript storage",
    )
    for phrase in required:
        assert phrase in text


def test_f1_preserves_r6_client_ledger_terminal_vs_running_fence():
    text = _contract()
    required = (
        "R6 ClientInvocationLedger terminal-vs-RUNNING retention authority",
        "RUNNING ledger rows preserve crash/ambiguity evidence",
        "only TERMINAL ledger rows may become CANDIDATE",
        "age-only rule must never classify a RUNNING ledger row as collectible",
    )
    for phrase in required:
        assert phrase in text


def test_f1_hard_scope_excludes_cross_track_and_recovery_ownership():
    text = _contract()
    required = (
        "no R12 leases",
        "no CAS asset/blob/reference/object-storage lifecycle",
        "no CTX persisted index/loader/retention ownership",
        "no Memory/Personalization lifecycle",
        "no speculative E2 query/index optimization",
        "no redefinition of R6 ClientInvocationLedger lifecycle/retry/TTL semantics",
        "no merge of PR #60 or PR #63",
    )
    for phrase in required:
        assert phrase in text
