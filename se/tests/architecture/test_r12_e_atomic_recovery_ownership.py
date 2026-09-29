from __future__ import annotations

import inspect

from se.src.infrastructure.storage.repositories.agent import AgentRepository
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.safe_point_reconstruction import (
    SafePointReconstructionError,
    reconstruct_r7c_safe_point_in_uow,
)
from se.src.runtimes.agent.task_budget import TaskBudgetService


def test_r12_e_repository_cas_freezes_exact_receipt_and_winner_fields():
    source = inspect.getsource(
        AgentRepository.compare_and_set_recovery_waiting_execution
    )
    for required in (
        "AgentExecutionRecord.revision == expected_revision",
        'AgentExecutionRecord.state == "RUNNING"',
        "AgentExecutionRecord.owner_instance_id",
        "== observed_owner_instance_id",
        "AgentExecutionRecord.lease_generation",
        "== observed_lease_generation",
        "AgentExecutionRecord.lease_expires_at",
        "== observed_lease_expires_at",
        "<= takeover_now_utc",
        '"state": "WAITING"',
        '"wait_reason": "RECOVERY"',
        '"owner_instance_id": None',
        '"lease_expires_at": None',
        "observed_lease_generation + 1",
    ):
        assert required in source

    for forbidden in (
        'next_values.update(values)',
        'next_values["state"] = values',
        'next_values["lease_generation"] = values',
    ):
        assert forbidden not in source


def test_r12_e_safe_point_helper_is_caller_uow_read_only_and_ordered():
    source = inspect.getsource(reconstruct_r7c_safe_point_in_uow)
    assert "tool_call_ids" in source
    assert "list_tool_calls" in source
    assert "get_tool_result" in source
    assert "capability_invocations" in source
    assert "materialize_checkpoint_transcript_in_uow" in source
    assert "commit_state" in source
    assert '"COMMITTED"' in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "created_at" not in source
    assert "list_records_for_execution" not in source


def test_r12_e_resume_and_recovery_share_the_same_r7c_helper():
    resume = inspect.getsource(DurableAgentStore.resume_execution)
    recovery = inspect.getsource(
        DurableAgentStore.commit_recovery_waiting_checkpoint
    )
    assert "reconstruct_r7c_safe_point_in_uow" in resume
    assert "reconstruct_r7c_safe_point_in_uow" in recovery


def test_r12_e_task_budget_keeps_canonical_release_identity():
    source = inspect.getsource(
        TaskBudgetService.recover_task_scoped_execution
    )
    assert "TaskBudgetReservationKind.RELEASE_EXECUTION" in source
    assert 'reservation_key = f"{execution_id}:{target_revision}"' in source
    assert "observed_lease_generation" in source
    assert "observed_lease_expires_at" in source
    assert "takeover_now_utc" in source
    assert "recovery:" not in source


def test_r12_e_recovery_surface_has_no_runtime_or_reconciliation_authority():
    recovery = inspect.getsource(
        DurableAgentStore.commit_recovery_waiting_checkpoint
    )
    task = inspect.getsource(TaskBudgetService.recover_task_scoped_execution)
    combined = recovery + task
    for forbidden in (
        "AgentRuntime",
        "execute_capability",
        "dispatch",
        "reconcile_remote",
        "resume_claim",
        "create_task(",
    ):
        assert forbidden not in combined


def test_r12_e_safe_point_error_is_explicit_fail_closed_contract():
    assert issubclass(SafePointReconstructionError, RuntimeError)
