import inspect
from pathlib import Path

from se.src.runtimes.agent.gc_dry_run import (
    GcDryRunClassification,
    GcDryRunItem,
    build_task_gc_dry_run_in_uow,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs/agent_execution_r11/R11_F1B_NON_DESTRUCTIVE_GC_DRY_RUN_98776B80.md"
)


def test_r11_f1b_contract_is_exact_parent_and_delete_free():
    text = CONTRACT.read_text(encoding="utf-8")
    source = inspect.getsource(build_task_gc_dry_run_in_uow)

    assert "98776b8038b4ffe0b31aaf616748bc38544f6979" in text
    assert "PRODUCTION DELETE CLOSED" in text
    assert "Age is not an input" in text
    assert "ClientInvocationLedger remains a separate R6" in text
    assert "delete(" not in source.lower()
    assert "session.delete" not in source.lower()
    assert "update(" not in source.lower()
    assert "insert(" not in source.lower()


def test_r11_f1b_source_freezes_f0_f1a_graph_families():
    source = inspect.getsource(build_task_gc_dry_run_in_uow)
    required = (
        "TaskBudgetReservationRecord",
        "AgentTaskBranchContextRecord",
        "AgentTaskForkAdmissionRecord",
        "AgentTaskRetryAdmissionRecord",
        "AgentTaskAggregateAdmissionRecord",
        "parent_execution_id",
        "retry_of_execution_id",
        "base_execution_id",
        "base_checkpoint_id",
        "AgentCheckpointPendingInvocationRecord",
        "AgentResumeClaimRecord",
        "AgentIterationRecord",
        "AgentToolCallRecord",
        "AgentToolResultRecord",
        "CapabilityInvocationRecord",
        "CapabilityInvocationAttemptRecord",
        "AgentTranscriptRepresentationRecord",
        "AgentTranscriptPayloadNodeRecord",
        "AgentTranscriptChunkRecord",
        "external_execution_semantic_reference",
        "external_branch_execution_reference",
        "external_aggregate_direct_reference",
        "external_aggregate_branch_reference",
        "external_aggregate_execution_reference",
        "external_aggregate_checkpoint_reference",
        "external_agent_tool_call_invocation_reference",
        "external_agent_tool_result_invocation_reference",
        "external_checkpoint_pending_invocation_reference",
        "invocation_execution_mismatch",
        "invocation_tool_call_mismatch",
        "invocation_capability_mismatch",
        "non_committed_authority",
    )
    for phrase in required:
        assert phrase in source


def test_r11_f1b_classification_surface_is_closed_and_explicit():
    assert {item.value for item in GcDryRunClassification} == {
        "RETAIN",
        "CANDIDATE",
        "FAIL_CLOSED",
    }
    item = GcDryRunItem(
        row_kind="task",
        row_identity="task-1",
        classification=GcDryRunClassification.RETAIN,
        reason_code="POLICY_RETAINED_HISTORY",
        root_or_edge_source="task:task-1",
    )
    assert item.classification is GcDryRunClassification.RETAIN
