from __future__ import annotations

import inspect
from pathlib import Path

from se.src.runtimes.agent.fork_planning import AgentForkPlanningService
from se.src.runtimes.agent.persistence import DurableAgentStore
from se.src.runtimes.agent.resume_planning import AgentResumePlanningService
from se.src.runtimes.agent.retry_planning import AgentRetryPlanningService
from se.src.runtimes.agent.task_budget import TaskBudgetService


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    ROOT
    / "docs/agent_execution_r11/"
    "R11_G_CONTENTION_LOAD_PERFORMANCE_E68FD96E.md"
)


def test_r11_g0_freezes_exact_entry_and_workload_matrix():
    text = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "main@e68fd96e701d90c4707a067487fe374234b11749",
        "main@d36b7e090c4b349ad6270e34752db9b29fe7e962",
        "Architecture #1315 GREEN/GREEN",
        "TaskBudget contention",
        "Long transcript / many-checkpoint workload",
        "Multi-branch workload",
        "Large pending invocation batch",
        "Concurrent RESUME / RETRY / FORK read pressure",
        "work/ae-r11-g-e68fd96e",
    ):
        assert phrase in text


def test_r11_g0_measures_canonical_production_surfaces():
    contract = CONTRACT.read_text(encoding="utf-8")

    surfaces = {
        "TaskBudgetService.reserve_branch_slot()": TaskBudgetService.reserve_branch_slot,
        "DurableAgentStore.commit_waiting_checkpoint()": (
            DurableAgentStore.commit_waiting_checkpoint
        ),
        "DurableAgentStore.list_task_branches()": DurableAgentStore.list_task_branches,
        "AgentResumePlanningService.build_resume_plan()": (
            AgentResumePlanningService.build_resume_plan
        ),
        "AgentRetryPlanningService.build_retry_plan()": (
            AgentRetryPlanningService.build_retry_plan
        ),
        "AgentForkPlanningService.build_fork_plan()": (
            AgentForkPlanningService.build_fork_plan
        ),
    }

    for name, method in surfaces.items():
        assert name in contract
        assert inspect.iscoroutinefunction(method)


def test_r11_g0_preserves_batched_pending_invocation_authority():
    source = inspect.getsource(DurableAgentStore.commit_waiting_checkpoint)
    contract = CONTRACT.read_text(encoding="utf-8")

    assert "stage_waiting_checkpoint" in source
    assert "pending_invocations" in source
    assert "Large pending invocation batch" in contract
    assert "R11-E batched persistence authority" in contract
    assert "transaction atomicity" in contract.lower()


def test_r11_g0_preserves_contention_and_lineage_boundaries():
    budget_source = inspect.getsource(TaskBudgetService.reserve_branch_slot)
    retry_source = inspect.getsource(AgentRetryPlanningService.build_retry_plan)
    fork_source = inspect.getsource(AgentForkPlanningService.build_fork_plan)
    contract = CONTRACT.read_text(encoding="utf-8")

    assert "_mutate_with_reservation" in budget_source
    assert "active_branches" in budget_source
    assert "source_execution_id" in retry_source
    assert "source_branch_id" in fork_source

    for phrase in (
        "TaskBudget CAS/retry/accounting semantics",
        "one TaskBudget shared by fork/retry/child execution lineage",
        "retry = same Task + same Branch + new execution",
        "fork = same Task + new Branch + new execution",
        "resume = same Task + same Branch + same execution",
        "no R12 lease/recovery authority",
        "no CAS physical-GC/provider-hydration ownership",
        "no CTX/Memory lifecycle ownership",
    ):
        assert phrase in contract


def test_r11_g0_keeps_timing_evidence_separate_from_correctness():
    text = CONTRACT.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    for phrase in (
        "p50 <= p95 <= p99",
        "Wall-clock values are retained as exact-run evidence",
        "not fixed cross-platform thresholds",
        "Any semantic/correctness failure is P0/P1 material regardless of timing",
    ):
        assert phrase in normalized
