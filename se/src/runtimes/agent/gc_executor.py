from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import delete, or_, select

from ...infrastructure.storage.models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentResumeClaimRecord,
    AgentTaskAggregateAdmissionRecord,
    AgentTaskBranchContextRecord,
    AgentTaskBranchRecord,
    AgentTaskForkAdmissionRecord,
    AgentTaskRecord,
    AgentTaskRetryAdmissionRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
    TaskBudgetRecord,
    TaskBudgetReservationRecord,
)
from ...infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationAttemptRecord,
    CapabilityInvocationRecord,
)
from ...infrastructure.storage.repositories.capability_invocations import (
    CapabilityInvocationRepository,
)
from .gc_dry_run import (
    GcDryRunClassification,
    build_task_gc_dry_run_in_uow,
)


class AgentGcCollectionError(RuntimeError):
    """Task-scoped R11-F1-C collection could not be proven safe."""


@dataclass(frozen=True)
class AgentGcCollectionResult:
    task_id: str
    fingerprint: str | None
    deleted_rows: tuple[tuple[str, int], ...]
    already_collected: bool = False

    @property
    def total_deleted(self) -> int:
        return sum(count for _, count in self.deleted_rows)


_CANDIDATE_KINDS = frozenset(
    {
        "task",
        "task_budget",
        "task_budget_reservation",
        "branch",
        "branch_context",
        "execution",
        "checkpoint",
        "checkpoint_pending_invocation",
        "resume_claim",
        "iteration",
        "tool_call",
        "tool_result",
        "capability_invocation",
        "capability_invocation_attempt",
        "fork_admission",
        "retry_admission",
        "aggregate_admission",
    }
)


def _candidate_counts(report) -> Counter[str]:
    return Counter(
        item.row_kind
        for item in report.items
        if item.classification is GcDryRunClassification.CANDIDATE
    )


def _candidate_ids(report, row_kind: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.row_identity
            for item in report.items
            if item.row_kind == row_kind
            and item.classification is GcDryRunClassification.CANDIDATE
        )
    )


def _validate_candidate_report(report) -> Counter[str]:
    if report.failed_closed:
        raise AgentGcCollectionError(
            "Fresh in-transaction R11-F1-B revalidation failed closed."
        )
    if not report.has_candidates:
        raise AgentGcCollectionError(
            "Fresh in-transaction R11-F1-B revalidation produced no candidates."
        )

    counts = _candidate_counts(report)
    unexpected = sorted(set(counts) - _CANDIDATE_KINDS)
    if unexpected:
        raise AgentGcCollectionError(
            "R11-F1-C candidate scope contains unopened row kinds: "
            + ", ".join(unexpected)
        )
    if counts.get("task", 0) != 1:
        raise AgentGcCollectionError(
            "R11-F1-C requires exactly one candidate Task owner."
        )
    return counts


async def _delete_exact(session, model, predicate, expected: int, label: str) -> int:
    if expected == 0:
        return 0
    result = await session.execute(delete(model).where(predicate))
    actual = int(result.rowcount or 0)
    if actual != expected:
        raise AgentGcCollectionError(
            f"R11-F1-C rowcount drift for {label}: expected {expected}, got {actual}."
        )
    return actual


def _branch_delete_order(rows: Iterable[AgentTaskBranchRecord]) -> tuple[str, ...]:
    parent_by_id = {
        str(row.branch_id): (
            None if row.parent_branch_id is None else str(row.parent_branch_id)
        )
        for row in rows
    }
    memo: dict[str, int] = {}

    def depth(branch_id: str, visiting: set[str]) -> int:
        if branch_id in memo:
            return memo[branch_id]
        if branch_id in visiting:
            raise AgentGcCollectionError(
                "R11-F1-C branch lineage cycle detected during deletion."
            )
        parent_id = parent_by_id.get(branch_id)
        if parent_id is None or parent_id not in parent_by_id:
            memo[branch_id] = 0
            return 0
        visiting.add(branch_id)
        value = 1 + depth(parent_id, visiting)
        visiting.remove(branch_id)
        memo[branch_id] = value
        return value

    for branch_id in parent_by_id:
        depth(branch_id, set())
    return tuple(
        sorted(parent_by_id, key=lambda item: (-memo[item], item))
    )


async def execute_task_gc_in_uow(
    uow,
    task_id: str,
    *,
    expected_fingerprint: str | None = None,
) -> AgentGcCollectionResult:
    """Collect one proven terminal Task graph inside the caller-owned transaction.

    The Task serialization fence is acquired before any destructive authority.
    A probe report identifies candidate invocation fences; after all invocation
    fences are acquired, F1-B is rerun and that second report is the only
    deletion authority. Any drift fails closed before mutation.
    """

    task_id = str(task_id)
    task = await uow.agents.lock_task_gc_serialization_fence(task_id)
    if task is None:
        return AgentGcCollectionResult(
            task_id=task_id,
            fingerprint=None,
            deleted_rows=(),
            already_collected=True,
        )

    probe = await build_task_gc_dry_run_in_uow(
        uow,
        task_id,
        policy_eligible_terminal=True,
    )
    _validate_candidate_report(probe)

    invocation_ids = _candidate_ids(probe, "capability_invocation")
    invocation_repository = getattr(uow, "capability_invocations", None)
    if invocation_repository is None:
        invocation_repository = CapabilityInvocationRepository(uow.session)

    for invocation_id in invocation_ids:
        await invocation_repository.lock_invocation_gc_serialization_fence(
            invocation_id
        )

    fresh = await build_task_gc_dry_run_in_uow(
        uow,
        task_id,
        policy_eligible_terminal=True,
    )
    counts = _validate_candidate_report(fresh)

    if fresh.fingerprint != probe.fingerprint:
        raise AgentGcCollectionError(
            "R11-F1-C graph/classification drifted while serialization fences "
            "were being acquired."
        )
    if (
        expected_fingerprint is not None
        and fresh.fingerprint != str(expected_fingerprint)
    ):
        raise AgentGcCollectionError(
            "Caller dry-run fingerprint is stale; fresh in-transaction "
            "revalidation does not match."
        )

    session = uow.session
    execution_ids = _candidate_ids(fresh, "execution")
    checkpoint_ids = _candidate_ids(fresh, "checkpoint")
    branch_ids = _candidate_ids(fresh, "branch")
    invocation_ids = _candidate_ids(fresh, "capability_invocation")

    deleted: list[tuple[str, int]] = []

    async def remove(model, predicate, kind: str) -> None:
        count = int(counts.get(kind, 0))
        actual = await _delete_exact(session, model, predicate, count, kind)
        if actual:
            deleted.append((kind, actual))

    # Provenance/admission rows carry RESTRICT references into branch,
    # execution, and checkpoint authority and therefore go first.
    await remove(
        AgentTaskAggregateAdmissionRecord,
        AgentTaskAggregateAdmissionRecord.task_id == task_id,
        "aggregate_admission",
    )
    await remove(
        AgentTaskForkAdmissionRecord,
        AgentTaskForkAdmissionRecord.task_id == task_id,
        "fork_admission",
    )
    await remove(
        AgentTaskRetryAdmissionRecord,
        AgentTaskRetryAdmissionRecord.task_id == task_id,
        "retry_admission",
    )

    if checkpoint_ids or execution_ids:
        predicates = []
        if checkpoint_ids:
            predicates.append(
                AgentResumeClaimRecord.checkpoint_id.in_(checkpoint_ids)
            )
        if execution_ids:
            predicates.append(
                AgentResumeClaimRecord.execution_id.in_(execution_ids)
            )
        await remove(
            AgentResumeClaimRecord,
            or_(*predicates),
            "resume_claim",
        )

    if checkpoint_ids:
        await remove(
            AgentCheckpointPendingInvocationRecord,
            AgentCheckpointPendingInvocationRecord.checkpoint_id.in_(
                checkpoint_ids
            ),
            "checkpoint_pending_invocation",
        )

    if execution_ids:
        await remove(
            AgentToolResultRecord,
            AgentToolResultRecord.execution_id.in_(execution_ids),
            "tool_result",
        )
        await remove(
            AgentToolCallRecord,
            AgentToolCallRecord.execution_id.in_(execution_ids),
            "tool_call",
        )
        await remove(
            AgentIterationRecord,
            AgentIterationRecord.execution_id.in_(execution_ids),
            "iteration",
        )

    if invocation_ids:
        await remove(
            CapabilityInvocationAttemptRecord,
            CapabilityInvocationAttemptRecord.invocation_id.in_(invocation_ids),
            "capability_invocation_attempt",
        )

    if branch_ids:
        await remove(
            AgentTaskBranchContextRecord,
            AgentTaskBranchContextRecord.branch_id.in_(branch_ids),
            "branch_context",
        )
        rows = list(
            (
                await session.execute(
                    select(AgentTaskBranchRecord).where(
                        AgentTaskBranchRecord.branch_id.in_(branch_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(rows) != int(counts.get("branch", 0)):
            raise AgentGcCollectionError(
                "R11-F1-C branch rowset drifted before child-first deletion."
            )
        for branch_id in _branch_delete_order(rows):
            actual = await _delete_exact(
                session,
                AgentTaskBranchRecord,
                AgentTaskBranchRecord.branch_id == branch_id,
                1,
                f"branch:{branch_id}",
            )
            if actual:
                deleted.append(("branch", actual))

    if checkpoint_ids:
        await remove(
            AgentExecutionCheckpointRecord,
            AgentExecutionCheckpointRecord.checkpoint_id.in_(checkpoint_ids),
            "checkpoint",
        )

    if invocation_ids:
        await remove(
            CapabilityInvocationRecord,
            CapabilityInvocationRecord.invocation_id.in_(invocation_ids),
            "capability_invocation",
        )

    if execution_ids:
        await remove(
            AgentExecutionRecord,
            AgentExecutionRecord.id.in_(execution_ids),
            "execution",
        )

    await remove(
        TaskBudgetReservationRecord,
        TaskBudgetReservationRecord.task_id == task_id,
        "task_budget_reservation",
    )
    await remove(
        TaskBudgetRecord,
        TaskBudgetRecord.task_id == task_id,
        "task_budget",
    )

    # The owner Task is always physically last.
    await remove(
        AgentTaskRecord,
        AgentTaskRecord.id == task_id,
        "task",
    )

    await session.flush()

    aggregated = Counter()
    for kind, count in deleted:
        aggregated[kind] += count
    for kind, expected in counts.items():
        if aggregated.get(kind, 0) != int(expected):
            raise AgentGcCollectionError(
                f"R11-F1-C final rowcount mismatch for {kind}: "
                f"expected {expected}, got {aggregated.get(kind, 0)}."
            )

    return AgentGcCollectionResult(
        task_id=task_id,
        fingerprint=fresh.fingerprint,
        deleted_rows=tuple(sorted(aggregated.items())),
    )


class AgentGcExecutor:
    """Transactional task-scoped R11-F1-C executor."""

    def __init__(self, uow_factory) -> None:
        self._uow_factory = uow_factory

    async def collect_task(
        self,
        task_id: str,
        *,
        expected_fingerprint: str | None = None,
    ) -> AgentGcCollectionResult:
        async with self._uow_factory() as uow:
            try:
                result = await execute_task_gc_in_uow(
                    uow,
                    task_id,
                    expected_fingerprint=expected_fingerprint,
                )
                await uow.commit()
                return result
            except Exception:
                rollback = getattr(uow, "rollback", None)
                if rollback is not None:
                    await rollback()
                raise
