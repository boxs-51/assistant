from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Sequence
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError

from ...domain.schemas.agent_execution import AgentExecutionLimits
from ...domain.schemas.task_budget import (
    TaskBudget,
    TaskBudgetLimits,
    TaskBudgetPolicy,
    TaskBudgetReservationKind,
    TaskBudgetState,
    normalize_task_budget_cost,
    task_budget_policy_fingerprint,
)
from .contracts.fork import (
    ForkAdmission,
    ForkPlan,
    fork_plan_fingerprint,
    fork_runtime_seed_payload,
)
from .contracts.branch_resolution import (
    BranchDiscardResult,
    TaskAdoptionResult,
)
from .contracts.aggregate import (
    AggregateAdmission,
    aggregate_fingerprint,
    aggregate_plan_fingerprint,
)
from .contracts.retry import (
    RetryAdmission,
    RetryPlan,
    retry_plan_fingerprint,
    retry_value_fingerprint,
)
from .serialization import to_json_safe
from .waiting_checkpoint import (
    stage_waiting_checkpoint,
    verify_committed_waiting_checkpoint,
)


class TaskBudgetError(RuntimeError):
    code = "TASK_BUDGET_ERROR"

    def __init__(self, message: str):
        super().__init__(f"{self.code}: {message}")


class TaskBudgetRequiredError(TaskBudgetError):
    code = "TASK_BUDGET_REQUIRED"


class TaskBudgetLegacyUninitializedError(TaskBudgetRequiredError):
    code = "TASK_BUDGET_LEGACY_UNINITIALIZED"


class TaskBudgetClosedError(TaskBudgetError):
    code = "TASK_BUDGET_CLOSED"


class TaskBudgetExceededError(TaskBudgetError):
    code = "TASK_BUDGET_EXCEEDED"


class TaskBudgetConflictError(TaskBudgetError):
    code = "TASK_BUDGET_CONFLICT"


class DelegationDepthExceededError(TaskBudgetError):
    code = "DELEGATION_DEPTH_EXCEEDED"


class AgentDelegationCycleError(TaskBudgetError):
    code = "AGENT_DELEGATION_CYCLE"


class ForkConsumeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ForkConsumeRejected(ForkConsumeError):
    pass


class ForkConsumeDeferred(ForkConsumeError):
    pass


class ForkConsumeConflict(ForkConsumeError):
    pass


class RetryConsumeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RetryConsumeRejected(RetryConsumeError):
    pass


class RetryConsumeDeferred(RetryConsumeError):
    pass


class RetryConsumeConflict(RetryConsumeError):
    pass


class BranchResolutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class AggregateAdmissionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class DelegationAdmission:
    task_id: str
    parent_execution_id: str | None
    branch_id: str | None
    child_agent_id: str
    delegation_depth: int
    ancestor_execution_ids: tuple[str, ...]
    ancestor_agent_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RootExecutionAdmission:
    task_id: str
    branch_id: str
    branch_revision: int
    execution_id: str
    execution_revision: int


_EXECUTION_JSON_FIELDS = frozenset({
    "request",
    "result",
    "context_state",
    "transcript",
    "inference_request",
    "inference_response",
})
_TASK_JSON_FIELDS = frozenset({"wait_reasons", "input", "output"})
_TASK_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _normalize_execution_store_values(
    values: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(values)
    for field in _EXECUTION_JSON_FIELDS:
        if field in normalized:
            normalized[field] = to_json_safe(
                normalized[field],
                path=f"agent_executions.{field}",
            )
    return normalized


def _normalize_task_store_values(
    values: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(values)
    for field in _TASK_JSON_FIELDS:
        if field in normalized:
            normalized[field] = to_json_safe(
                normalized[field],
                path=f"agent_tasks.{field}",
            )
    return normalized


def _reservation_fingerprint(
    kind: TaskBudgetReservationKind,
    reservation_key: str,
    payload: dict[str, Any],
) -> str:
    encoded = json.dumps(
        {
            "kind": kind.value,
            "reservation_key": reservation_key,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _root_branch_id(task_id: str) -> str:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:40]
    return f"r8_root_{digest}"


def _budget_from_record(record) -> TaskBudget:
    limits = TaskBudgetLimits(
        max_total_executions=record.max_total_executions,
        max_active_executions=record.max_active_executions,
        max_active_branches=record.max_active_branches,
        max_parallel_agents=record.max_parallel_agents,
        max_total_tool_calls=record.max_total_tool_calls,
        max_total_inference_calls=record.max_total_inference_calls,
        max_total_tokens=record.max_total_tokens,
        max_total_cost_usd=record.max_total_cost_usd,
        max_delegation_depth=record.max_delegation_depth,
    )
    return TaskBudget(
        task_id=record.task_id,
        revision=record.revision,
        state=record.state,
        limits=limits,
        policy_version=record.policy_version,
        policy_fingerprint=record.policy_fingerprint,
        deny_recursive_agent_cycle=record.deny_recursive_agent_cycle,
        used_executions=record.used_executions,
        active_executions=record.active_executions,
        active_branches=record.active_branches,
        active_parallel_agents=record.active_parallel_agents,
        used_tool_calls=record.used_tool_calls,
        used_inference_calls=record.used_inference_calls,
        used_tokens=record.used_tokens,
        used_cost_usd=record.used_cost_usd,
        created_at=getattr(record, "created_at", None),
        updated_at=getattr(record, "updated_at", None),
        closed_at=getattr(record, "closed_at", None),
    )


async def reconcile_multibranch_task_activity_in_uow(
    uow,
    *,
    task_id: str,
    locked_task=None,
):
    """Derive only nonterminal R8 activity from OPEN branch heads.

    Returns None only when the AgentTask CAS loses and the caller should retry
    or roll back its surrounding authority transaction.
    """

    task = locked_task
    if task is None:
        task = await uow.agents.get_task_for_update(task_id)
    if task is None:
        raise TaskBudgetRequiredError(f"Unknown AgentTask: {task_id}")
    if str(task.id) != str(task_id):
        raise TaskBudgetConflictError(
            "Locked AgentTask does not match activity task_id."
        )
    current_task_state = str(task.status)
    if current_task_state in _TASK_TERMINAL_STATES:
        return task

    branches = await uow.agents.list_task_branches(task_id)
    if len(branches) <= 1:
        return task

    current_executions = []
    for branch in branches:
        if str(branch.resolution_state) != "OPEN":
            continue
        execution_id = branch.current_execution_id
        if execution_id is None:
            continue
        execution = await uow.agents.get_execution(execution_id)
        if (
            execution is None
            or execution.task_id != task_id
            or execution.branch_id != branch.branch_id
        ):
            raise TaskBudgetConflictError(
                "TaskBranch current_execution_id has invalid durable lineage."
            )
        current_executions.append(execution)

    target_state: str | None = None
    wait_reasons: list[str] = []
    if any(
        str(execution.state) in {"CREATED", "RUNNING"}
        for execution in current_executions
    ):
        target_state = "RUNNING"
    else:
        waiting_reasons: set[str] = set()
        for execution in current_executions:
            state = str(execution.state)
            if state not in {"WAITING", "WAITING_FOR_CONNECTION"}:
                continue
            reason = str(execution.wait_reason or "")
            if not reason and state == "WAITING_FOR_CONNECTION":
                reason = "CONNECTION"
            if reason:
                waiting_reasons.add(reason)
        if waiting_reasons:
            target_state = "WAITING"
            wait_reasons = sorted(waiting_reasons)

    # All OPEN branch heads terminal (or unresolved/missing heads): R9 owns
    # result resolution. Preserve the existing nonterminal Task + OPEN budget.
    if target_state is None:
        return task

    current_reasons = sorted(str(item) for item in (task.wait_reasons or []))
    if (
        current_task_state == target_state
        and current_reasons == wait_reasons
    ):
        return task

    updated = await uow.agents.compare_and_set_task_activity(
        task_id,
        int(task.revision),
        target_state=target_state,
        wait_reasons=wait_reasons,
    )
    return updated


async def prepare_resume_capacity_in_uow(
    uow,
    *,
    task_id: str,
    execution_id: str,
    source_revision: int,
    delegated: bool,
) -> TaskBudget:
    """Stage R7-F resume capacity inside the caller's transaction.

    This primitive deliberately does not mutate AgentExecution and does not
    commit. The caller must couple the staged TaskBudget/reservation writes
    with its own WAITING -> RUNNING authority CAS.
    """

    if source_revision < 0:
        raise ValueError("source_revision must be non-negative")

    task = await uow.agents.get_task(task_id)
    if task is None:
        raise TaskBudgetRequiredError(f"Unknown AgentTask: {task_id}")
    if str(task.status) in _TASK_TERMINAL_STATES:
        raise TaskBudgetClosedError(
            f"Terminal AgentTask cannot resume execution: {task_id}"
        )

    budget_record = await uow.agents.get_task_budget(task_id)
    if budget_record is None:
        if await uow.agents.has_execution_for_task(task_id):
            raise TaskBudgetLegacyUninitializedError(
                "Task has durable execution history but no TaskBudget."
            )
        raise TaskBudgetRequiredError(f"TaskBudget missing: {task_id}")

    budget = _budget_from_record(budget_record)
    if budget.state is not TaskBudgetState.OPEN:
        raise TaskBudgetClosedError(f"TaskBudget is CLOSED: {task_id}")

    reservation_key = f"{execution_id}:{source_revision}"
    payload = {
        "execution_id": execution_id,
        "source_revision": source_revision,
        "delegated": delegated,
    }
    fingerprint = _reservation_fingerprint(
        TaskBudgetReservationKind.RESUME_EXECUTION,
        reservation_key,
        payload,
    )
    existing = await uow.agents.get_task_budget_reservation(
        task_id,
        TaskBudgetReservationKind.RESUME_EXECUTION.value,
        reservation_key,
    )
    if existing is not None:
        if existing.payload_fingerprint != fingerprint:
            raise TaskBudgetConflictError(
                "resume reservation_key was reused with a different payload"
            )
        raise TaskBudgetConflictError(
            "resume capacity reservation already exists before execution claim"
        )

    if budget.active_executions >= budget.limits.max_active_executions:
        raise TaskBudgetExceededError("max_active_executions reached")
    if (
        delegated
        and budget.active_parallel_agents >= budget.limits.max_parallel_agents
    ):
        raise TaskBudgetExceededError("max_parallel_agents reached")

    updated = await uow.agents.compare_and_set_task_budget(
        task_id,
        budget.revision,
        {
            "active_executions": budget.active_executions + 1,
            "active_parallel_agents": (
                budget.active_parallel_agents + 1
                if delegated
                else budget.active_parallel_agents
            ),
        },
    )
    if updated is None:
        raise TaskBudgetConflictError(
            f"Stale TaskBudget revision during resume: {task_id}@{budget.revision}"
        )

    await uow.agents.save_task_budget_reservation(
        {
            "task_id": task_id,
            "kind": TaskBudgetReservationKind.RESUME_EXECUTION.value,
            "reservation_key": reservation_key,
            "payload_fingerprint": fingerprint,
        }
    )
    return _budget_from_record(updated)


class TaskBudgetService:
    """Durable TaskBudget policy and transaction coordinator.

    R5-B exposes reservation primitives only.  R5-C is responsible for wiring
    them into AgentRuntime state transitions.
    """

    def __init__(
        self,
        uow_factory,
        *,
        default_limits: TaskBudgetLimits | None = None,
        default_policy: TaskBudgetPolicy | None = None,
        max_conflict_retries: int = 8,
    ) -> None:
        self._uow_factory = uow_factory
        self._default_limits = default_limits
        self._default_policy = default_policy
        self._max_conflict_retries = max(1, int(max_conflict_retries))

    @property
    def default_limits(self) -> TaskBudgetLimits | None:
        return self._default_limits

    @property
    def default_policy(self) -> TaskBudgetPolicy | None:
        return self._default_policy

    async def resolve_delegation_admission(
        self,
        task_id: str,
        *,
        parent_execution_id: str | None,
        child_agent_id: str,
    ) -> DelegationAdmission:
        """Derive delegation depth and Agent ancestry from durable lineage.

        Caller metadata is deliberately not accepted here.  R5-D treats
        AgentExecution.parent_execution_id + agent_id as the only ancestry
        authority that survives restart and multi-worker execution.
        """
        if not task_id:
            raise TaskBudgetRequiredError(
                "Delegated Agent execution requires a durable task_id."
            )
        if not child_agent_id:
            raise ValueError("child_agent_id must be non-empty")

        async with self._uow_factory() as uow:
            budget_record = await uow.agents.get_task_budget(task_id)
            if budget_record is None:
                if await uow.agents.has_execution_for_task(task_id):
                    raise TaskBudgetLegacyUninitializedError(
                        "Task has durable execution history but no TaskBudget."
                    )
                raise TaskBudgetRequiredError(
                    f"TaskBudget missing: {task_id}"
                )
            budget = _budget_from_record(budget_record)
            self._require_open(budget)

            if parent_execution_id is None:
                admission = DelegationAdmission(
                    task_id=task_id,
                    parent_execution_id=None,
                    branch_id=None,
                    child_agent_id=child_agent_id,
                    delegation_depth=0,
                    ancestor_execution_ids=(),
                    ancestor_agent_ids=(),
                )
                await uow.commit()
                return admission

            ancestor_execution_ids: list[str] = []
            ancestor_agent_ids: list[str] = []
            seen_execution_ids: set[str] = set()
            durable_branch_id: str | None = None
            current_execution_id: str | None = parent_execution_id

            while current_execution_id is not None:
                if current_execution_id in seen_execution_ids:
                    raise TaskBudgetConflictError(
                        "Durable Agent delegation lineage contains an "
                        "execution-id cycle."
                    )
                seen_execution_ids.add(current_execution_id)

                execution = await uow.agents.get_execution(
                    current_execution_id
                )
                if execution is None:
                    raise TaskBudgetConflictError(
                        "Delegation parent execution does not exist: "
                        f"{current_execution_id}"
                    )
                if execution.task_id != task_id:
                    raise TaskBudgetConflictError(
                        "Delegation parent belongs to a different AgentTask."
                    )
                if not execution.branch_id:
                    raise TaskBudgetConflictError(
                        "Delegation parent has no normalized TaskBranch."
                    )
                if durable_branch_id is None:
                    durable_branch_id = str(execution.branch_id)
                elif str(execution.branch_id) != durable_branch_id:
                    raise TaskBudgetConflictError(
                        "Delegation ancestry crosses TaskBranch boundaries."
                    )

                ancestor_execution_ids.append(execution.id)
                ancestor_agent_ids.append(execution.agent_id)

                # Every durable AgentExecution on this Task was admitted
                # through used_executions. Exceeding that count proves the
                # lineage graph is inconsistent even before a cycle repeats.
                if len(ancestor_execution_ids) > budget.used_executions:
                    raise TaskBudgetConflictError(
                        "Durable Agent delegation lineage exceeds the "
                        "TaskBudget execution history."
                    )

                current_execution_id = execution.parent_execution_id

            delegation_depth = len(ancestor_execution_ids)
            if (
                delegation_depth
                > budget.limits.max_delegation_depth
            ):
                raise DelegationDepthExceededError(
                    f"delegation_depth={delegation_depth} exceeds "
                    f"{budget.limits.max_delegation_depth}"
                )

            if (
                budget.deny_recursive_agent_cycle
                and child_agent_id in ancestor_agent_ids
            ):
                raise AgentDelegationCycleError(
                    f"Agent '{child_agent_id}' already exists in durable "
                    "delegation ancestry."
                )

            if durable_branch_id is None:
                raise TaskBudgetConflictError(
                    "Delegated execution could not resolve durable branch."
                )
            branch = await uow.agents.get_task_branch(durable_branch_id)
            if (
                branch is None
                or branch.task_id != task_id
                or str(branch.resolution_state) != "OPEN"
            ):
                raise TaskBudgetConflictError(
                    "Delegated execution requires an OPEN normalized "
                    "TaskBranch."
                )

            admission = DelegationAdmission(
                task_id=task_id,
                parent_execution_id=parent_execution_id,
                branch_id=durable_branch_id,
                child_agent_id=child_agent_id,
                delegation_depth=delegation_depth,
                ancestor_execution_ids=tuple(ancestor_execution_ids),
                ancestor_agent_ids=tuple(ancestor_agent_ids),
            )
            await uow.commit()
            return admission

    async def create_task_with_budget(
        self,
        task_values: dict[str, Any],
        *,
        limits: TaskBudgetLimits | None = None,
        policy: TaskBudgetPolicy | None = None,
    ):
        """Atomically persist one AgentTask and its initial TaskBudget."""
        task_id = str(task_values.get("id") or "")
        if not task_id:
            raise ValueError("task_values.id must be non-empty")
        limits = limits or self._default_limits
        policy = policy or self._default_policy
        if limits is None or policy is None:
            raise TaskBudgetRequiredError(
                "Task creation requires an application TaskBudget policy."
            )

        fingerprint = task_budget_policy_fingerprint(limits, policy)
        normalized_task = dict(task_values)
        for field in ("wait_reasons", "input", "output"):
            if field in normalized_task:
                normalized_task[field] = to_json_safe(
                    normalized_task[field],
                    path=f"agent_tasks.{field}",
                )

        async with self._uow_factory() as uow:
            if await uow.agents.get_task(task_id) is not None:
                raise TaskBudgetConflictError(
                    f"AgentTask already exists: {task_id}"
                )
            task = await uow.agents.save_task(normalized_task)
            await uow.agents.save_task_budget(
                self._new_budget_values(
                    task_id,
                    limits,
                    policy,
                    fingerprint,
                )
            )
            await uow.commit()
            return task

    async def transition_task(
        self,
        task_id: str,
        *,
        allowed_source_states: Sequence[str],
        target_state: str,
        values: dict[str, Any] | None = None,
    ):
        """CAS one nonterminal AgentTask while TaskBudget remains OPEN.

        If a concurrent terminal transition already won, return that durable
        winner unchanged so stale completion/start logic cannot resurrect it.
        """
        target_state = str(target_state)
        if target_state in _TASK_TERMINAL_STATES:
            raise ValueError(
                "terminal target requires terminalize_task()"
            )
        allowed = {str(item) for item in allowed_source_states}
        if not allowed:
            raise ValueError("allowed_source_states must not be empty")
        normalized = _normalize_task_store_values(values or {})
        normalized["status"] = target_state

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    task = await uow.agents.get_task_for_update(task_id)
                    if task is None:
                        raise TaskBudgetRequiredError(
                            f"Unknown AgentTask: {task_id}"
                        )
                    current_state = str(task.status)
                    if current_state in _TASK_TERMINAL_STATES:
                        await uow.commit()
                        return task

                    budget_record = await uow.agents.get_task_budget_for_update(
                        task_id
                    )
                    if budget_record is None:
                        if await uow.agents.has_execution_for_task(task_id):
                            raise TaskBudgetLegacyUninitializedError(
                                "Task has durable execution history but no "
                                "TaskBudget."
                            )
                        raise TaskBudgetRequiredError(
                            f"TaskBudget missing: {task_id}"
                        )
                    budget = _budget_from_record(budget_record)
                    self._require_open(budget)

                    if current_state not in allowed and current_state != target_state:
                        raise TaskBudgetConflictError(
                            f"AgentTask {task_id} is {current_state}, "
                            f"expected one of {sorted(allowed)}"
                        )

                    if target_state == "WAITING":
                        branches = await uow.agents.list_task_branches(task_id)
                        if len(branches) > 1:
                            aggregate = (
                                await reconcile_multibranch_task_activity_in_uow(
                                    uow,
                                    task_id=task_id,
                                    locked_task=task,
                                )
                            )
                            if aggregate is None:
                                await uow.rollback()
                                continue
                            await uow.commit()
                            return aggregate

                    if current_state == target_state:
                        await uow.commit()
                        return task

                    updated = await uow.agents.compare_and_set_task(
                        task_id,
                        task.revision,
                        normalized,
                    )
                    if updated is None:
                        await uow.rollback()
                        continue
                    await uow.commit()
                    return updated
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"AgentTask CAS conflicts exhausted for {task_id}"
        )

    async def terminalize_task(
        self,
        task_id: str,
        *,
        allowed_source_states: Sequence[str],
        target_state: str,
        values: dict[str, Any] | None = None,
    ):
        """Atomically terminalize AgentTask and close its TaskBudget.

        Any already-terminal Task is authoritative.  This makes completion
        after cancellation and repeated cancellation fail closed without
        resurrecting or rewriting the durable winner.
        """
        target_state = str(target_state)
        if target_state not in _TASK_TERMINAL_STATES:
            raise ValueError(
                "target_state must be COMPLETED, FAILED or CANCELLED"
            )
        allowed = {str(item) for item in allowed_source_states}
        if not allowed:
            raise ValueError("allowed_source_states must not be empty")
        normalized = _normalize_task_store_values(values or {})
        normalized["status"] = target_state

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    task = await uow.agents.get_task_for_update(task_id)
                    if task is None:
                        raise TaskBudgetRequiredError(
                            f"Unknown AgentTask: {task_id}"
                        )
                    budget_record = await uow.agents.get_task_budget_for_update(
                        task_id
                    )
                    if budget_record is None:
                        if await uow.agents.has_execution_for_task(task_id):
                            raise TaskBudgetLegacyUninitializedError(
                                "Task has durable execution history but no "
                                "TaskBudget."
                            )
                        raise TaskBudgetRequiredError(
                            f"TaskBudget missing: {task_id}"
                        )
                    budget = _budget_from_record(budget_record)
                    current_state = str(task.status)

                    if current_state in _TASK_TERMINAL_STATES:
                        if budget.state is TaskBudgetState.OPEN:
                            closed = (
                                await uow.agents.compare_and_set_task_budget(
                                    task_id,
                                    budget.revision,
                                    {
                                        "state": TaskBudgetState.CLOSED.value,
                                        "closed_at": datetime.now(timezone.utc),
                                    },
                                )
                            )
                            if closed is None:
                                await uow.rollback()
                                continue
                        await uow.commit()
                        return task

                    if budget.state is not TaskBudgetState.OPEN:
                        raise TaskBudgetConflictError(
                            "Nonterminal AgentTask has CLOSED TaskBudget."
                        )

                    branches = await uow.agents.list_task_branches(task_id)
                    if len(branches) > 1:
                        aggregate = (
                            await reconcile_multibranch_task_activity_in_uow(
                                uow,
                                task_id=task_id,
                                locked_task=task,
                            )
                        )
                        if aggregate is None:
                            await uow.rollback()
                            continue
                        await uow.commit()
                        return aggregate

                    if current_state not in allowed:
                        raise TaskBudgetConflictError(
                            f"AgentTask {task_id} is {current_state}, "
                            f"expected one of {sorted(allowed)}"
                        )

                    updated_task = await uow.agents.compare_and_set_task(
                        task_id,
                        task.revision,
                        normalized,
                    )
                    if updated_task is None:
                        await uow.rollback()
                        continue

                    updated_budget = (
                        await uow.agents.compare_and_set_task_budget(
                            task_id,
                            budget.revision,
                            {
                                "state": TaskBudgetState.CLOSED.value,
                                "closed_at": datetime.now(timezone.utc),
                            },
                        )
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue

                    await uow.commit()
                    return updated_task
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"AgentTask/TaskBudget terminal CAS conflicts exhausted for "
            f"{task_id}"
        )

    async def cancel_task(
        self,
        task_id: str,
        *,
        values: dict[str, Any] | None = None,
    ):
        """Cancel Task authority and settle exact dormant R8 FORK executions.

        Fork consume precharges active execution capacity before a process-local
        runner exists. Cancellation therefore races activation on E2 revision 1
        and releases capacity only for the exact ForkAdmission-backed
        preactivation rows it wins.
        """

        normalized = _normalize_task_store_values(values or {})
        normalized["status"] = "CANCELLED"

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    # Frozen R8-F serialization order:
                    # Task -> TaskBudget -> Branch(es) -> E2 revision CAS.
                    task = await uow.agents.get_task_for_update(task_id)
                    if task is None:
                        raise TaskBudgetRequiredError(
                            f"Unknown AgentTask: {task_id}"
                        )
                    budget_record = await uow.agents.get_task_budget_for_update(
                        task_id
                    )
                    if budget_record is None:
                        if await uow.agents.has_execution_for_task(task_id):
                            raise TaskBudgetLegacyUninitializedError(
                                "Task has durable execution history but no "
                                "TaskBudget."
                            )
                        raise TaskBudgetRequiredError(
                            f"TaskBudget missing: {task_id}"
                        )
                    budget = _budget_from_record(budget_record)
                    current_state = str(task.status)

                    # A different terminal winner remains authoritative.
                    if (
                        current_state in _TASK_TERMINAL_STATES
                        and current_state != "CANCELLED"
                    ):
                        if budget.state is TaskBudgetState.OPEN:
                            closed = await uow.agents.compare_and_set_task_budget(
                                task_id,
                                budget.revision,
                                {
                                    "state": TaskBudgetState.CLOSED.value,
                                    "closed_at": datetime.now(timezone.utc),
                                },
                            )
                            if closed is None:
                                await uow.rollback()
                                continue
                        await uow.commit()
                        return task

                    if (
                        current_state not in _TASK_TERMINAL_STATES
                        and current_state
                        not in {"ASSIGNED", "RUNNING", "WAITING"}
                    ):
                        raise TaskBudgetConflictError(
                            f"AgentTask {task_id} is {current_state}, "
                            "expected ASSIGNED/RUNNING/WAITING."
                        )
                    if (
                        current_state != "CANCELLED"
                        and budget.state is not TaskBudgetState.OPEN
                    ):
                        raise TaskBudgetConflictError(
                            "Nonterminal AgentTask has CLOSED TaskBudget."
                        )

                    now_utc = datetime.now(timezone.utc)
                    cancelled_forks = 0
                    cancelled_retries = 0
                    cancelled_aggregates = 0
                    cancelled_delegated = 0

                    # Lock every TaskBranch once, in the repository's canonical
                    # branch_id order, before inspecting admission receipts.
                    # FORK/RETRY receipts are ordered by creation/request id and
                    # therefore must never drive branch-row lock acquisition.
                    locked_branches = (
                        await uow.agents.list_task_branches_for_update(task_id)
                    )
                    branch_map = {
                        item.branch_id: item for item in locked_branches
                    }

                    receipts = await uow.agents.list_task_fork_admissions(
                        task_id
                    )
                    for receipt in receipts:
                        branch = branch_map.get(receipt.branch_id)
                        execution = await uow.agents.get_execution(
                            receipt.execution_id
                        )
                        if branch is None or execution is None:
                            raise TaskBudgetConflictError(
                                "ForkAdmission durable graph is incomplete "
                                f"for {receipt.execution_id}."
                            )

                        exact_preactivation = (
                            branch.task_id == task_id
                            and branch.current_execution_id
                            == receipt.execution_id
                            and receipt.source_branch_id
                            == branch.parent_branch_id
                            and receipt.source_execution_id
                            == branch.base_execution_id
                            and receipt.source_checkpoint_id
                            == branch.base_checkpoint_id
                            and execution.task_id == task_id
                            and execution.branch_id == receipt.branch_id
                            and execution.base_execution_id
                            == receipt.source_execution_id
                            and execution.base_checkpoint_id
                            == receipt.source_checkpoint_id
                            and execution.retry_of_execution_id is None
                            and str(execution.state) == "RUNNING"
                            and int(execution.revision) == 1
                            and execution.current_checkpoint_id is None
                            and execution.bound_client_id is None
                            and execution.bound_connection_id is None
                        )
                        if not exact_preactivation:
                            continue

                        cancelled = (
                            await uow.agents
                            .compare_and_set_fork_preactivation_cancel(
                                execution.id,
                                task_id=task_id,
                                branch_id=receipt.branch_id,
                                base_execution_id=receipt.source_execution_id,
                                base_checkpoint_id=receipt.source_checkpoint_id,
                                completed_at=now_utc,
                            )
                        )
                        if cancelled is None:
                            # Activation won revision 1. Do not mutate or
                            # account the durable winner here.
                            continue
                        cancelled_forks += 1
                        if execution.parent_execution_id is not None:
                            cancelled_delegated += 1

                    retry_receipts = (
                        await uow.agents.list_task_retry_admissions(task_id)
                    )
                    for receipt in retry_receipts:
                        branch = branch_map.get(receipt.branch_id)
                        execution = await uow.agents.get_execution(
                            receipt.execution_id
                        )
                        if branch is None or execution is None:
                            raise TaskBudgetConflictError(
                                "RetryAdmission durable graph is incomplete "
                                f"for {receipt.execution_id}."
                            )
                        exact_preactivation = (
                            branch.task_id == task_id
                            and branch.current_execution_id
                            == receipt.execution_id
                            and execution.task_id == task_id
                            and execution.branch_id == receipt.branch_id
                            and execution.retry_of_execution_id
                            == receipt.source_execution_id
                            and str(execution.state) == "RUNNING"
                            and int(execution.revision) == 1
                            and execution.current_checkpoint_id is None
                            and execution.bound_client_id is None
                            and execution.bound_connection_id is None
                        )
                        if not exact_preactivation:
                            continue
                        cancelled = (
                            await uow.agents
                            .compare_and_set_retry_preactivation_cancel(
                                execution.id,
                                task_id=task_id,
                                branch_id=receipt.branch_id,
                                source_execution_id=(
                                    receipt.source_execution_id
                                ),
                                completed_at=now_utc,
                            )
                        )
                        if cancelled is None:
                            continue
                        cancelled_retries += 1
                        if execution.parent_execution_id is not None:
                            cancelled_delegated += 1

                    aggregate_receipts = (
                        await uow.agents.list_task_aggregate_admissions(task_id)
                    )
                    for receipt in aggregate_receipts:
                        branch = branch_map.get(receipt.target_branch_id)
                        execution = await uow.agents.get_execution(
                            receipt.execution_id
                        )
                        if branch is None or execution is None:
                            raise TaskBudgetConflictError(
                                "AggregateAdmission durable graph is incomplete "
                                f"for {receipt.execution_id}."
                            )
                        exact_preactivation = (
                            branch.task_id == task_id
                            and branch.current_execution_id
                            == receipt.execution_id
                            and execution.task_id == task_id
                            and execution.branch_id
                            == receipt.target_branch_id
                            and execution.retry_of_execution_id is None
                            and str(execution.state) == "RUNNING"
                            and int(execution.revision) == 1
                            and execution.current_checkpoint_id is None
                            and execution.bound_client_id is None
                            and execution.bound_connection_id is None
                        )
                        if not exact_preactivation:
                            continue
                        cancelled = await uow.agents.compare_and_set_execution(
                            execution.id,
                            1,
                            {
                                "state": "CANCELLED",
                                "wait_reason": None,
                                "wait_expires_at": None,
                                "error": (
                                    "TASK_CANCELLED_BEFORE_AGGREGATE_ACTIVATION"
                                ),
                                "completed_at": now_utc,
                            },
                        )
                        if cancelled is None:
                            continue
                        cancelled_aggregates += 1
                        if execution.parent_execution_id is not None:
                            cancelled_delegated += 1

                    cancelled_dormant = (
                        cancelled_forks
                        + cancelled_retries
                        + cancelled_aggregates
                    )
                    if budget.active_executions < cancelled_dormant:
                        raise TaskBudgetConflictError(
                            "Dormant execution cancellation would underflow "
                            "active_executions."
                        )
                    if (
                        budget.active_parallel_agents
                        < cancelled_delegated
                    ):
                        raise TaskBudgetConflictError(
                            "Fork cancellation would underflow "
                            "active_parallel_agents."
                        )

                    updated_task = task
                    if current_state != "CANCELLED":
                        updated_task = await uow.agents.compare_and_set_task(
                            task_id,
                            task.revision,
                            normalized,
                        )
                        if updated_task is None:
                            await uow.rollback()
                            continue

                    budget_values = {
                        "state": TaskBudgetState.CLOSED.value,
                        "closed_at": (
                            budget.closed_at
                            if budget.closed_at is not None
                            else now_utc
                        ),
                        "active_executions": (
                            budget.active_executions - cancelled_dormant
                        ),
                        "active_parallel_agents": (
                            budget.active_parallel_agents
                            - cancelled_delegated
                        ),
                    }
                    updated_budget = (
                        await uow.agents.compare_and_set_task_budget(
                            task_id,
                            budget.revision,
                            budget_values,
                        )
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue

                    await uow.commit()
                    return updated_task
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"AgentTask cancellation conflicts exhausted for {task_id}"
        )

    async def discard_branch(
        self,
        task_id: str,
        branch_id: str,
        *,
        target_user_id: str,
    ) -> BranchDiscardResult:
        """Atomically resolve one non-final OPEN branch as DISCARDED."""

        if not task_id or not branch_id or not target_user_id:
            raise BranchResolutionError(
                "BRANCH_RESOLUTION_CONFLICT",
                "Task, branch and principal identifiers are required.",
            )
        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    task = await uow.agents.get_task_for_update(task_id)
                    budget = await uow.agents.get_task_budget_for_update(task_id)
                    branches = await uow.agents.list_task_branches_for_update(
                        task_id
                    )
                    selected = next(
                        (item for item in branches if item.branch_id == branch_id),
                        None,
                    )
                    if task is None or budget is None or selected is None:
                        raise BranchResolutionError(
                            "BRANCH_RESOLUTION_CONFLICT",
                            "Task, budget or selected branch is missing.",
                        )
                    if str(task.created_by) != target_user_id:
                        raise BranchResolutionError(
                            "BRANCH_RESOLUTION_CONFLICT",
                            "Authenticated principal does not own the Task.",
                        )
                    state = str(selected.resolution_state)
                    if state == "DISCARDED":
                        result = BranchDiscardResult(
                            task_id=task_id,
                            branch_id=branch_id,
                            branch_revision=int(selected.revision),
                            task_budget_revision=int(budget.revision),
                        )
                        await uow.commit()
                        return result
                    if state != "OPEN":
                        raise BranchResolutionError(
                            "BRANCH_NOT_OPEN",
                            f"TaskBranch is already {state}.",
                        )
                    if str(task.status) in _TASK_TERMINAL_STATES or str(budget.state) != "OPEN":
                        raise BranchResolutionError(
                            "TASK_ALREADY_RESOLVED",
                            "Terminal Task authority forbids branch DISCARD.",
                        )
                    open_branches = [
                        item
                        for item in branches
                        if str(item.resolution_state) == "OPEN"
                    ]
                    if len(open_branches) <= 1:
                        raise BranchResolutionError(
                            "BRANCH_DISCARD_LAST_OPEN_FORBIDDEN",
                            "The final OPEN branch cannot be discarded.",
                        )
                    if int(budget.active_branches) != len(open_branches):
                        raise BranchResolutionError(
                            "BRANCH_RESOLUTION_CONFLICT",
                            "TaskBudget active_branches differs from branch authority.",
                        )

                    # DISCARD must revoke any dormant execution authority before
                    # the branch stops participating in Task activity. Active
                    # RUNNING/WAITING owners are rejected fail-closed; only an
                    # immutable admission-backed RUNNING@1 preactivation may be
                    # cancelled in this same Task/Budget/Branch transaction.
                    release_active = 0
                    release_parallel = 0
                    if selected.current_execution_id is not None:
                        execution = await uow.agents.get_execution_for_update(
                            selected.current_execution_id
                        )
                        if (
                            execution is None
                            or execution.task_id != task_id
                            or execution.branch_id != branch_id
                        ):
                            raise BranchResolutionError(
                                "BRANCH_RESOLUTION_CONFLICT",
                                "TaskBranch current execution has invalid lineage.",
                            )
                        execution_state = str(execution.state)
                        if execution_state in {
                            "COMPLETED",
                            "FAILED",
                            "CANCELLED",
                            "TIMEOUT",
                        }:
                            pass
                        elif (
                            execution_state == "RUNNING"
                            and int(execution.revision) == 1
                        ):
                            fork_receipt = (
                                await uow.agents
                                .get_task_fork_admission_by_execution(execution.id)
                            )
                            retry_receipt = (
                                await uow.agents
                                .get_task_retry_admission_by_execution(execution.id)
                            )
                            aggregate_receipt = (
                                await uow.agents
                                .get_task_aggregate_admission_by_execution(execution.id)
                            )
                            matching_receipts = 0
                            if (
                                fork_receipt is not None
                                and fork_receipt.task_id == task_id
                                and fork_receipt.branch_id == branch_id
                                and fork_receipt.execution_id == execution.id
                            ):
                                matching_receipts += 1
                            if (
                                retry_receipt is not None
                                and retry_receipt.task_id == task_id
                                and retry_receipt.branch_id == branch_id
                                and retry_receipt.execution_id == execution.id
                            ):
                                matching_receipts += 1
                            if (
                                aggregate_receipt is not None
                                and aggregate_receipt.task_id == task_id
                                and aggregate_receipt.target_branch_id == branch_id
                                and aggregate_receipt.execution_id == execution.id
                            ):
                                matching_receipts += 1
                            if (
                                matching_receipts != 1
                                or execution.current_checkpoint_id is not None
                                or execution.bound_client_id is not None
                                or execution.bound_connection_id is not None
                            ):
                                raise BranchResolutionError(
                                    "BRANCH_EXECUTION_ACTIVE",
                                    "Branch execution is not a dormant admitted preactivation.",
                                )
                            cancelled = await uow.agents.compare_and_set_execution(
                                execution.id,
                                1,
                                {
                                    "state": "CANCELLED",
                                    "wait_reason": None,
                                    "wait_expires_at": None,
                                    "error": (
                                        "BRANCH_DISCARDED_BEFORE_ACTIVATION"
                                    ),
                                    "completed_at": datetime.now(timezone.utc),
                                },
                            )
                            if cancelled is None:
                                await uow.rollback()
                                continue
                            release_active = 1
                            if execution.parent_execution_id is not None:
                                release_parallel = 1
                        else:
                            raise BranchResolutionError(
                                "BRANCH_EXECUTION_ACTIVE",
                                "RUNNING/WAITING branch execution must settle before DISCARD.",
                            )

                    if int(budget.active_executions) < release_active:
                        raise BranchResolutionError(
                            "BRANCH_RESOLUTION_CONFLICT",
                            "DISCARD would underflow active_executions.",
                        )
                    if int(budget.active_parallel_agents) < release_parallel:
                        raise BranchResolutionError(
                            "BRANCH_RESOLUTION_CONFLICT",
                            "DISCARD would underflow active_parallel_agents.",
                        )

                    changed = await uow.agents.compare_and_set_task_branch(
                        branch_id,
                        int(selected.revision),
                        {"resolution_state": "DISCARDED"},
                    )
                    if changed is None:
                        await uow.rollback()
                        continue
                    updated_budget = await uow.agents.compare_and_set_task_budget(
                        task_id,
                        int(budget.revision),
                        {
                            "active_branches": int(budget.active_branches) - 1,
                            "active_executions": (
                                int(budget.active_executions) - release_active
                            ),
                            "active_parallel_agents": (
                                int(budget.active_parallel_agents)
                                - release_parallel
                            ),
                        },
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue
                    result = BranchDiscardResult(
                        task_id=task_id,
                        branch_id=branch_id,
                        branch_revision=int(changed.revision),
                        task_budget_revision=int(updated_budget.revision),
                    )
                    await uow.commit()
                    return result
            except OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    continue
                raise
        raise BranchResolutionError(
            "BRANCH_RESOLUTION_CONFLICT",
            f"DISCARD conflicts exhausted for {task_id}/{branch_id}.",
        )

    async def adopt_branch(
        self,
        task_id: str,
        branch_id: str,
        *,
        target_user_id: str,
    ) -> TaskAdoptionResult:
        """Commit the sole Task result authority and invalidate resumability."""

        if not task_id or not branch_id or not target_user_id:
            raise BranchResolutionError(
                "TASK_RESOLUTION_CONFLICT",
                "Task, branch and principal identifiers are required.",
            )
        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    # Frozen R9 order: Task -> Budget -> sorted Branches ->
                    # selected Execution -> CREATED claims -> CAS mutations.
                    task = await uow.agents.get_task_for_update(task_id)
                    budget = await uow.agents.get_task_budget_for_update(task_id)
                    branches = await uow.agents.list_task_branches_for_update(
                        task_id
                    )
                    selected = next(
                        (item for item in branches if item.branch_id == branch_id),
                        None,
                    )
                    if task is None or budget is None or selected is None:
                        raise BranchResolutionError(
                            "TASK_RESOLUTION_CONFLICT",
                            "Task, budget or selected branch is missing.",
                        )
                    if str(task.created_by) != target_user_id:
                        raise BranchResolutionError(
                            "TASK_RESOLUTION_CONFLICT",
                            "Authenticated principal does not own the Task.",
                        )
                    selected_state = str(selected.resolution_state)
                    if str(task.status) in _TASK_TERMINAL_STATES:
                        if (
                            str(task.status) == "COMPLETED"
                            and selected_state == "ADOPTED"
                            and selected.current_execution_id is not None
                        ):
                            result = TaskAdoptionResult(
                                task_id=task_id,
                                task_revision=int(task.revision),
                                selected_branch_id=branch_id,
                                selected_execution_id=selected.current_execution_id,
                                task_budget_revision=int(budget.revision),
                                superseded_branch_ids=tuple(
                                    item.branch_id
                                    for item in branches
                                    if str(item.resolution_state) == "SUPERSEDED"
                                ),
                                rejected_resume_claim_ids=(),
                            )
                            await uow.commit()
                            return result
                        raise BranchResolutionError(
                            "TASK_ALREADY_RESOLVED",
                            f"Task is already {task.status}.",
                        )
                    if str(budget.state) != "OPEN":
                        raise BranchResolutionError(
                            "TASK_RESOLUTION_CONFLICT",
                            "Nonterminal Task has a CLOSED TaskBudget.",
                        )
                    if selected_state != "OPEN":
                        raise BranchResolutionError(
                            "BRANCH_NOT_OPEN",
                            f"Selected TaskBranch is already {selected_state}.",
                        )
                    if selected.current_execution_id is None:
                        raise BranchResolutionError(
                            "BRANCH_RESULT_NOT_COMPLETED",
                            "Selected branch has no current execution.",
                        )
                    open_branches = [
                        item
                        for item in branches
                        if str(item.resolution_state) == "OPEN"
                    ]
                    if int(budget.active_branches) != len(open_branches):
                        raise BranchResolutionError(
                            "TASK_RESOLUTION_CONFLICT",
                            "TaskBudget active_branches differs from branch authority.",
                        )

                    # Lock every current OPEN-branch execution after all Branch
                    # rows, in deterministic execution-id order.  ADOPT may
                    # detach an already-owned RUNNING@2+ loser and let it
                    # controlled-complete, but it must never strand an
                    # admission-backed RUNNING@1 that has no runtime owner.
                    current_execution_ids = sorted(
                        {
                            str(item.current_execution_id)
                            for item in open_branches
                            if item.current_execution_id is not None
                        }
                    )
                    locked_executions = {}
                    for current_execution_id in current_execution_ids:
                        locked_executions[current_execution_id] = (
                            await uow.agents.get_execution_for_update(
                                current_execution_id
                            )
                        )

                    execution = locked_executions.get(
                        str(selected.current_execution_id)
                    )
                    if (
                        execution is None
                        or execution.task_id != task_id
                        or execution.branch_id != branch_id
                        or str(execution.state) != "COMPLETED"
                        or execution.result is None
                    ):
                        raise BranchResolutionError(
                            "BRANCH_RESULT_NOT_COMPLETED",
                            "Selected current execution has no durable COMPLETED result.",
                        )

                    now_utc = datetime.now(timezone.utc)
                    release_active = 0
                    release_parallel = 0
                    preactivation_conflict = False
                    for loser in open_branches:
                        if loser.branch_id == branch_id:
                            continue
                        loser_execution_id = loser.current_execution_id
                        if loser_execution_id is None:
                            continue
                        loser_execution = locked_executions.get(
                            str(loser_execution_id)
                        )
                        if (
                            loser_execution is None
                            or loser_execution.task_id != task_id
                            or loser_execution.branch_id != loser.branch_id
                        ):
                            raise BranchResolutionError(
                                "TASK_RESOLUTION_CONFLICT",
                                "Loser branch current execution has invalid lineage.",
                            )
                        if not (
                            str(loser_execution.state) == "RUNNING"
                            and int(loser_execution.revision) == 1
                        ):
                            continue

                        fork_receipt = (
                            await uow.agents.get_task_fork_admission_by_execution(
                                loser_execution.id
                            )
                        )
                        retry_receipt = (
                            await uow.agents.get_task_retry_admission_by_execution(
                                loser_execution.id
                            )
                        )
                        aggregate_receipt = (
                            await uow.agents
                            .get_task_aggregate_admission_by_execution(
                                loser_execution.id
                            )
                        )
                        matching_receipts = 0
                        if (
                            fork_receipt is not None
                            and fork_receipt.task_id == task_id
                            and fork_receipt.branch_id == loser.branch_id
                            and fork_receipt.execution_id == loser_execution.id
                        ):
                            matching_receipts += 1
                        if (
                            retry_receipt is not None
                            and retry_receipt.task_id == task_id
                            and retry_receipt.branch_id == loser.branch_id
                            and retry_receipt.execution_id == loser_execution.id
                        ):
                            matching_receipts += 1
                        if (
                            aggregate_receipt is not None
                            and aggregate_receipt.task_id == task_id
                            and aggregate_receipt.target_branch_id
                            == loser.branch_id
                            and aggregate_receipt.execution_id
                            == loser_execution.id
                        ):
                            matching_receipts += 1

                        if matching_receipts == 0:
                            # RUNNING@1 is also used by pre-R9/root lifecycles.
                            # Without an immutable FORK/RETRY/AGGREGATE receipt
                            # R9 has no authority to reinterpret or cancel it;
                            # preserve the established controlled-completion
                            # behavior for that owner.
                            continue
                        if (
                            matching_receipts != 1
                            or loser_execution.current_checkpoint_id is not None
                            or loser_execution.bound_client_id is not None
                            or loser_execution.bound_connection_id is not None
                        ):
                            raise BranchResolutionError(
                                "TASK_RESOLUTION_CONFLICT",
                                "Admission-backed loser RUNNING@1 execution "
                                "has ambiguous or activated authority.",
                            )

                        cancelled = await uow.agents.compare_and_set_execution(
                            loser_execution.id,
                            1,
                            {
                                "state": "CANCELLED",
                                "wait_reason": None,
                                "wait_expires_at": None,
                                "error": "TASK_ADOPTED_BEFORE_ACTIVATION",
                                "completed_at": now_utc,
                            },
                        )
                        if cancelled is None:
                            preactivation_conflict = True
                            break
                        release_active += 1
                        if loser_execution.parent_execution_id is not None:
                            release_parallel += 1
                    if preactivation_conflict:
                        await uow.rollback()
                        continue
                    if int(budget.active_executions) < release_active:
                        raise BranchResolutionError(
                            "TASK_RESOLUTION_CONFLICT",
                            "ADOPT would underflow active_executions.",
                        )
                    if (
                        int(budget.active_parallel_agents)
                        < release_parallel
                    ):
                        raise BranchResolutionError(
                            "TASK_RESOLUTION_CONFLICT",
                            "ADOPT would underflow active_parallel_agents.",
                        )

                    claims = (
                        await uow.agents
                        .list_created_resume_claims_for_task_for_update(
                            task_id
                        )
                    )
                    updated_task = await uow.agents.compare_and_set_task(
                        task_id,
                        int(task.revision),
                        {
                            "status": "COMPLETED",
                            "wait_reasons": [],
                            "output": to_json_safe(
                                dict(execution.result),
                                path="agent_tasks.output",
                            ),
                            "error": None,
                        },
                    )
                    if updated_task is None:
                        await uow.rollback()
                        continue
                    updated_budget = await uow.agents.compare_and_set_task_budget(
                        task_id,
                        int(budget.revision),
                        {
                            "state": "CLOSED",
                            "closed_at": now_utc,
                            "active_branches": 0,
                            "active_executions": (
                                int(budget.active_executions) - release_active
                            ),
                            "active_parallel_agents": (
                                int(budget.active_parallel_agents)
                                - release_parallel
                            ),
                        },
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue
                    superseded: list[str] = []
                    branch_conflict = False
                    for item in open_branches:
                        target = "ADOPTED" if item.branch_id == branch_id else "SUPERSEDED"
                        changed = await uow.agents.compare_and_set_task_branch(
                            item.branch_id,
                            int(item.revision),
                            {"resolution_state": target},
                        )
                        if changed is None:
                            branch_conflict = True
                            break
                        if target == "SUPERSEDED":
                            superseded.append(item.branch_id)
                    if branch_conflict:
                        await uow.rollback()
                        continue
                    rejected: list[str] = []
                    claim_conflict = False
                    for claim in claims:
                        changed = await uow.agents.compare_and_set_resume_claim(
                            claim.claim_id,
                            int(claim.revision),
                            "CREATED",
                            {
                                "state": "REJECTED",
                                "rejection_code": "TASK_RESOLVED",
                                "rejected_at": now_utc,
                            },
                        )
                        if changed is None:
                            claim_conflict = True
                            break
                        rejected.append(claim.claim_id)
                    if claim_conflict:
                        await uow.rollback()
                        continue
                    result = TaskAdoptionResult(
                        task_id=task_id,
                        task_revision=int(updated_task.revision),
                        selected_branch_id=branch_id,
                        selected_execution_id=execution.id,
                        task_budget_revision=int(updated_budget.revision),
                        superseded_branch_ids=tuple(superseded),
                        rejected_resume_claim_ids=tuple(rejected),
                    )
                    await uow.commit()
                    return result
            except OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    continue
                raise
        raise BranchResolutionError(
            "TASK_RESOLUTION_CONFLICT",
            f"ADOPT conflicts exhausted for {task_id}/{branch_id}.",
        )

    async def aggregate_branches(
        self,
        task_id: str,
        *,
        aggregate_request_id: str,
        target_branch_id: str,
        source_branch_ids: Sequence[str],
        target_user_id: str,
    ) -> AggregateAdmission:
        """Atomically admit one explicit aggregate execution; never ADOPT."""

        ordered_ids = tuple(str(item) for item in source_branch_ids)
        if (
            not task_id
            or not aggregate_request_id
            or not target_branch_id
            or not target_user_id
            or len(ordered_ids) < 2
            or len(set(ordered_ids)) != len(ordered_ids)
            or target_branch_id not in ordered_ids
        ):
            raise AggregateAdmissionError(
                "AGGREGATE_INPUT_CONFLICT",
                "AGGREGATE requires a target included in at least two unique ordered sources.",
            )
        execution_id = f"r9_aggregate_{uuid4().hex}"
        admitted_at = datetime.now(timezone.utc)
        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    task = await uow.agents.get_task_for_update(task_id)
                    budget = await uow.agents.get_task_budget_for_update(task_id)
                    branches = await uow.agents.list_task_branches_for_update(
                        task_id
                    )
                    branch_map = {item.branch_id: item for item in branches}
                    receipt = await uow.agents.get_task_aggregate_admission(
                        task_id, aggregate_request_id
                    )
                    if receipt is not None:
                        committed_ids = tuple(
                            str(item["branch_id"])
                            for item in receipt.source_branch_snapshots
                        )
                        if (
                            receipt.target_branch_id != target_branch_id
                            or committed_ids != ordered_ids
                            or receipt.created_by != target_user_id
                        ):
                            raise AggregateAdmissionError(
                                "AGGREGATE_REQUEST_CONFLICT",
                                "aggregate_request_id already committed with different semantics.",
                            )
                        execution = await uow.agents.get_execution(
                            receipt.execution_id
                        )
                        target = branch_map.get(receipt.target_branch_id)
                        if execution is None or target is None or execution.task_id != task_id:
                            raise AggregateAdmissionError(
                                "AGGREGATE_INPUT_CONFLICT",
                                "Committed aggregate durable graph is incomplete.",
                            )
                        result = AggregateAdmission(
                            task_id=task_id,
                            aggregate_request_id=aggregate_request_id,
                            plan_fingerprint=receipt.plan_fingerprint,
                            runtime_seed_fingerprint=(
                                receipt.runtime_seed_fingerprint
                            ),
                            target_branch_id=receipt.target_branch_id,
                            branch_revision=int(target.revision),
                            execution_id=receipt.execution_id,
                            execution_revision=int(execution.revision),
                            source_branch_ids=committed_ids,
                            task_revision=int(task.revision),
                            task_budget_revision=int(budget.revision),
                        )
                        await uow.commit()
                        return result
                    if task is None or budget is None:
                        raise AggregateAdmissionError(
                            "AGGREGATE_INPUT_CONFLICT",
                            "Task or TaskBudget is missing.",
                        )
                    if str(task.created_by) != target_user_id:
                        raise AggregateAdmissionError(
                            "AGGREGATE_INPUT_CONFLICT",
                            "Authenticated principal does not own the Task.",
                        )
                    if str(task.status) in _TASK_TERMINAL_STATES or str(budget.state) != "OPEN":
                        raise AggregateAdmissionError(
                            "TASK_ALREADY_RESOLVED",
                            "Terminal Task authority forbids AGGREGATE.",
                        )
                    selected_branches = [branch_map.get(item) for item in ordered_ids]
                    if any(item is None for item in selected_branches):
                        raise AggregateAdmissionError(
                            "AGGREGATE_INPUT_CONFLICT",
                            "An aggregate source branch is missing.",
                        )
                    if any(
                        str(item.resolution_state) != "OPEN"
                        or item.current_execution_id is None
                        for item in selected_branches
                    ):
                        raise AggregateAdmissionError(
                            "AGGREGATE_INPUT_CONFLICT",
                            "Every aggregate source must be an OPEN branch with a current execution.",
                        )
                    execution_ids = sorted(
                        str(item.current_execution_id)
                        for item in selected_branches
                    )
                    execution_map = {}
                    for current_id in execution_ids:
                        execution_map[current_id] = (
                            await uow.agents.get_execution_for_update(current_id)
                        )
                    source_executions = [
                        execution_map[str(item.current_execution_id)]
                        for item in selected_branches
                    ]
                    if any(
                        item is None
                        or item.task_id != task_id
                        or item.branch_id != branch.branch_id
                        or str(item.state) != "COMPLETED"
                        or item.result is None
                        for branch, item in zip(
                            selected_branches, source_executions
                        )
                    ):
                        raise AggregateAdmissionError(
                            "AGGREGATE_INPUT_CONFLICT",
                            "Every aggregate source head must have a durable COMPLETED result.",
                        )
                    if (
                        int(budget.used_executions)
                        >= int(budget.max_total_executions)
                        or int(budget.active_executions)
                        >= int(budget.max_active_executions)
                    ):
                        raise AggregateAdmissionError(
                            "AGGREGATE_BUDGET_EXCEEDED",
                            "TaskBudget execution capacity is exhausted.",
                        )
                    target_branch = branch_map[target_branch_id]
                    target_execution = execution_map[
                        str(target_branch.current_execution_id)
                    ]
                    delegated = target_execution.parent_execution_id is not None
                    if delegated and int(budget.active_parallel_agents) >= int(
                        budget.max_parallel_agents
                    ):
                        raise AggregateAdmissionError(
                            "AGGREGATE_BUDGET_EXCEEDED",
                            "TaskBudget parallel-Agent capacity is exhausted.",
                        )
                    branch_snapshots = [
                        {
                            "branch_id": branch.branch_id,
                            "revision": int(branch.revision),
                            "resolution_state": str(branch.resolution_state),
                            "current_execution_id": branch.current_execution_id,
                        }
                        for branch in selected_branches
                    ]
                    result_fingerprints = [
                        aggregate_fingerprint(dict(item.result))
                        for item in source_executions
                    ]
                    execution_snapshots = [
                        {
                            "execution_id": item.id,
                            "revision": int(item.revision),
                            "state": str(item.state),
                            "result_fingerprint": fingerprint,
                        }
                        for item, fingerprint in zip(
                            source_executions, result_fingerprints
                        )
                    ]
                    target_context_state = dict(
                        target_execution.context_state or {}
                    )
                    if "limits" not in target_context_state:
                        fork_receipt = (
                            await uow.agents
                            .get_task_fork_admission_by_execution(
                                target_execution.id
                            )
                        )
                        seed = (
                            dict(fork_receipt.runtime_seed_json or {})
                            if fork_receipt is not None
                            else {}
                        )
                        if seed:
                            target_context_state = {
                                "request_id": seed.get("request_id"),
                                "workflow_id": seed.get("workflow_id"),
                                "metadata": dict(seed.get("metadata") or {}),
                                "causation_id": seed.get("causation_id"),
                                "trace_id": seed.get("trace_id"),
                                "limits": dict(seed.get("limits") or {}),
                            }
                    try:
                        limits = AgentExecutionLimits.model_validate(
                            target_context_state["limits"]
                        )
                        fresh_budget = float(limits.timeout_seconds)
                    except Exception as exc:
                        raise AggregateAdmissionError(
                            "AGGREGATE_INPUT_CONFLICT",
                            "Target branch has no valid durable runtime seed.",
                        ) from exc
                    aggregate_request = {
                        "aggregate_request_id": aggregate_request_id,
                        "target_branch_id": target_branch_id,
                        "source_results": [
                            {
                                "branch_id": branch.branch_id,
                                "execution_id": execution.id,
                                "result": dict(execution.result),
                            }
                            for branch, execution in zip(
                                selected_branches, source_executions
                            )
                        ],
                    }
                    runtime_seed_fingerprint = aggregate_fingerprint(
                        {
                            "context_state": target_context_state,
                            "request": aggregate_request,
                            "fresh_active_budget_seconds": fresh_budget,
                        }
                    )
                    plan_fingerprint = aggregate_plan_fingerprint(
                        task_id=task_id,
                        target_branch_id=target_branch_id,
                        source_branch_snapshots=branch_snapshots,
                        source_execution_snapshots=execution_snapshots,
                        result_fingerprints=result_fingerprints,
                        runtime_seed_fingerprint=runtime_seed_fingerprint,
                        created_by=target_user_id,
                    )
                    execution_values = _normalize_execution_store_values(
                        {
                            "id": execution_id,
                            "session_id": str(task.session_id),
                            "agent_id": target_execution.agent_id,
                            "task_id": task_id,
                            "branch_id": target_branch_id,
                            "parent_execution_id": (
                                target_execution.parent_execution_id
                            ),
                            "retry_of_execution_id": None,
                            "base_execution_id": target_execution.id,
                            "base_checkpoint_id": (
                                target_execution.current_checkpoint_id
                            ),
                            "correlation_id": target_execution.correlation_id,
                            "state": "RUNNING",
                            "wait_reason": None,
                            "revision": 1,
                            "current_checkpoint_id": None,
                            "bound_client_id": None,
                            "bound_connection_id": None,
                            "remaining_active_budget_seconds": fresh_budget,
                            "wait_expires_at": None,
                            "request": aggregate_request,
                            "result": None,
                            "context_state": target_context_state,
                            "transcript": list(
                                target_execution.transcript or []
                            ),
                            "inference_request": None,
                            "inference_response": None,
                            "error": None,
                            "started_at": admitted_at,
                            "completed_at": None,
                        }
                    )
                    updated_task = await uow.agents.compare_and_set_task(
                        task_id,
                        int(task.revision),
                        {"status": "RUNNING", "wait_reasons": []},
                    )
                    if updated_task is None:
                        await uow.rollback()
                        continue
                    budget_updates = {
                        "used_executions": int(budget.used_executions) + 1,
                        "active_executions": int(budget.active_executions) + 1,
                    }
                    if delegated:
                        budget_updates["active_parallel_agents"] = (
                            int(budget.active_parallel_agents) + 1
                        )
                    updated_budget = await uow.agents.compare_and_set_task_budget(
                        task_id, int(budget.revision), budget_updates
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue
                    await uow.agents.save_execution(execution_values)
                    updated_branch = await uow.agents.compare_and_set_task_branch(
                        target_branch_id,
                        int(target_branch.revision),
                        {"current_execution_id": execution_id},
                    )
                    if updated_branch is None:
                        await uow.rollback()
                        continue
                    reservation_payload = {
                        "aggregate_request_id": aggregate_request_id,
                        "execution_id": execution_id,
                        "plan_fingerprint": plan_fingerprint,
                    }
                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": task_id,
                            "kind": TaskBudgetReservationKind.NEW_EXECUTION.value,
                            "reservation_key": execution_id,
                            "payload_fingerprint": _reservation_fingerprint(
                                TaskBudgetReservationKind.NEW_EXECUTION,
                                execution_id,
                                reservation_payload,
                            ),
                        }
                    )
                    await uow.agents.save_task_aggregate_admission(
                        {
                            "task_id": task_id,
                            "aggregate_request_id": aggregate_request_id,
                            "plan_fingerprint": plan_fingerprint,
                            "runtime_seed_fingerprint": (
                                runtime_seed_fingerprint
                            ),
                            "target_branch_id": target_branch_id,
                            "execution_id": execution_id,
                            "source_branch_snapshots": branch_snapshots,
                            "source_execution_snapshots": execution_snapshots,
                            "result_fingerprints": result_fingerprints,
                            "created_by": target_user_id,
                        }
                    )
                    result = AggregateAdmission(
                        task_id=task_id,
                        aggregate_request_id=aggregate_request_id,
                        plan_fingerprint=plan_fingerprint,
                        runtime_seed_fingerprint=runtime_seed_fingerprint,
                        target_branch_id=target_branch_id,
                        branch_revision=int(updated_branch.revision),
                        execution_id=execution_id,
                        execution_revision=1,
                        source_branch_ids=ordered_ids,
                        task_revision=int(updated_task.revision),
                        task_budget_revision=int(updated_budget.revision),
                    )
                    await uow.commit()
                    return result
            except IntegrityError:
                continue
            except OperationalError as exc:
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    continue
                raise
        raise AggregateAdmissionError(
            "AGGREGATE_REQUEST_CONFLICT",
            f"AGGREGATE conflicts exhausted for {task_id}.",
        )

    @staticmethod
    def _retry_consume_error(exc: Exception) -> RetryConsumeError:
        from .retry_planning import RetryPlanDeferred

        code = str(getattr(exc, "code", "RETRY_CONSUME_CONFLICT"))
        message = str(exc)
        if isinstance(exc, RetryPlanDeferred):
            return RetryConsumeDeferred(code, message)
        if code == "RETRY_PLAN_FINGERPRINT_INVALID":
            return RetryConsumeRejected(code, message)
        if any(
            token in code
            for token in (
                "CONFLICT",
                "STALE",
                "CHANGED",
                "NOT_CURRENT",
            )
        ):
            return RetryConsumeConflict(code, message)
        return RetryConsumeRejected(code, message)

    async def _replay_retry_admission_in_uow(
        self,
        uow,
        *,
        plan: RetryPlan,
        receipt,
    ) -> RetryAdmission:
        if (
            receipt.plan_fingerprint != plan.plan_fingerprint
            or receipt.branch_id != plan.branch_id
            or receipt.source_execution_id != plan.source_execution_id
            or receipt.source_checkpoint_id != plan.source_checkpoint_id
            or receipt.created_by != plan.target_user_id
        ):
            raise RetryConsumeConflict(
                "RETRY_REQUEST_CONFLICT",
                "retry_request_id already committed with different semantics.",
            )

        task = await uow.agents.get_task(plan.task_id)
        budget = await uow.agents.get_task_budget(plan.task_id)
        branch = await uow.agents.get_task_branch(receipt.branch_id)
        source = await uow.agents.get_execution(receipt.source_execution_id)
        execution = await uow.agents.get_execution(receipt.execution_id)
        reservation = await uow.agents.get_task_budget_reservation(
            plan.task_id,
            TaskBudgetReservationKind.NEW_EXECUTION.value,
            receipt.execution_id,
        )

        expected_base_checkpoint_id = (
            plan.source_checkpoint_id
            if plan.source_checkpoint_id is not None
            else plan.base_checkpoint_id
        )
        corrupt = (
            task is None
            or budget is None
            or branch is None
            or source is None
            or execution is None
            or reservation is None
            or branch.task_id != plan.task_id
            or source.task_id != plan.task_id
            or source.branch_id != plan.branch_id
            or execution.task_id != plan.task_id
            or execution.branch_id != plan.branch_id
            or execution.retry_of_execution_id != plan.source_execution_id
            or execution.parent_execution_id != plan.parent_execution_id
            or execution.base_execution_id != plan.base_execution_id
            or execution.base_checkpoint_id != expected_base_checkpoint_id
            or (
                plan.source_checkpoint_id is not None
                and retry_value_fingerprint(
                    list(execution.transcript or [])
                )
                != plan.source_checkpoint_transcript_fingerprint
            )
            or retry_value_fingerprint(
                dict(execution.request or {})
            ) != plan.request_fingerprint
        )
        if corrupt:
            raise RetryConsumeConflict(
                "RETRY_ADMISSION_CORRUPT",
                "Committed RetryAdmission no longer matches durable outputs.",
            )

        return RetryAdmission(
            task_id=plan.task_id,
            retry_request_id=plan.retry_request_id,
            plan_fingerprint=plan.plan_fingerprint,
            branch_id=receipt.branch_id,
            branch_revision=int(branch.revision),
            source_execution_id=receipt.source_execution_id,
            source_checkpoint_id=receipt.source_checkpoint_id,
            execution_id=receipt.execution_id,
            execution_revision=int(execution.revision),
            task_revision=int(task.revision),
            task_budget_revision=int(budget.revision),
        )

    async def _probe_retry_replay(
        self,
        plan: RetryPlan,
    ) -> RetryAdmission | None:
        async with self._uow_factory() as uow:
            receipt = await uow.agents.get_task_retry_admission(
                plan.task_id,
                plan.retry_request_id,
            )
            if receipt is None:
                await uow.commit()
                return None
            admission = await self._replay_retry_admission_in_uow(
                uow,
                plan=plan,
                receipt=receipt,
            )
            await uow.commit()
            return admission

    async def consume_retry_plan(
        self,
        plan: RetryPlan,
    ) -> RetryAdmission:
        """Atomically admit one same-Branch retry without starting runtime."""

        from .retry_planning import (
            RetryPlanDeferred,
            RetryPlanRejected,
            revalidate_retry_plan_in_uow,
        )

        if retry_plan_fingerprint(plan) != plan.plan_fingerprint:
            raise RetryConsumeRejected(
                "RETRY_PLAN_FINGERPRINT_INVALID",
                "RetryPlan semantic fingerprint no longer matches its payload.",
            )

        execution_id = f"r9_retry_{uuid4().hex}"
        started_at = datetime.now(timezone.utc)

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    receipt = await uow.agents.get_task_retry_admission(
                        plan.task_id,
                        plan.retry_request_id,
                    )
                    if receipt is not None:
                        admission = await self._replay_retry_admission_in_uow(
                            uow,
                            plan=plan,
                            receipt=receipt,
                        )
                        await uow.commit()
                        return admission

                    try:
                        snapshot = await revalidate_retry_plan_in_uow(
                            uow,
                            plan,
                        )
                    except (RetryPlanRejected, RetryPlanDeferred) as exc:
                        await uow.rollback()
                        replay = await self._probe_retry_replay(plan)
                        if replay is not None:
                            return replay
                        raise self._retry_consume_error(exc) from exc

                    source = snapshot.execution
                    delegated = snapshot.delegated
                    retry_base_checkpoint_id = (
                        plan.source_checkpoint_id
                        if plan.source_checkpoint_id is not None
                        else plan.base_checkpoint_id
                    )
                    normalized_execution_values = (
                        _normalize_execution_store_values(
                            {
                                "id": execution_id,
                                "session_id": plan.session_id,
                                "agent_id": plan.source_agent_id,
                                "task_id": plan.task_id,
                                "branch_id": plan.branch_id,
                                "parent_execution_id":
                                    plan.parent_execution_id,
                                "retry_of_execution_id":
                                    plan.source_execution_id,
                                "base_execution_id":
                                    plan.base_execution_id,
                                "base_checkpoint_id":
                                    retry_base_checkpoint_id,
                                "correlation_id": plan.correlation_id,
                                "state": "RUNNING",
                                "wait_reason": None,
                                "revision": 1,
                                "current_checkpoint_id": None,
                                "bound_client_id": None,
                                "bound_connection_id": None,
                                "remaining_active_budget_seconds":
                                    plan.fresh_active_budget_seconds,
                                "wait_expires_at": None,
                                "request": dict(source.request or {}),
                                "result": None,
                                # R9-C reconstructs the runtime only from
                                # committed admission outputs.  Copy the
                                # terminal source seed instead of consulting
                                # process memory after commit/restart.
                                "context_state": dict(
                                    snapshot.runtime_context_state
                                ),
                                "transcript": (
                                    [
                                        item.model_dump(mode="json")
                                        for item in (
                                            snapshot.checkpoint_transcript or ()
                                        )
                                    ]
                                    if snapshot.checkpoint is not None
                                    else list(source.transcript or [])
                                ),
                                "inference_request": None,
                                "inference_response": None,
                                "error": None,
                                "started_at": started_at,
                                "completed_at": None,
                            }
                        )
                    )
                    reservation_execution_values = to_json_safe(
                        normalized_execution_values,
                        path="task_budget.retry_execution_reservation",
                    )
                    execution_payload = {
                        "retry_request_id": plan.retry_request_id,
                        "plan_fingerprint": plan.plan_fingerprint,
                        "source_execution_id": plan.source_execution_id,
                        "execution_id": execution_id,
                        "execution_values": reservation_execution_values,
                        "delegated": delegated,
                    }
                    execution_fingerprint = _reservation_fingerprint(
                        TaskBudgetReservationKind.NEW_EXECUTION,
                        execution_id,
                        execution_payload,
                    )

                    # Same-Branch RETRY changes the branch-head activity graph.
                    # Bump Task revision even if visible status stays RUNNING.
                    updated_task = await uow.agents.compare_and_set_task(
                        plan.task_id,
                        plan.expected_task_revision,
                        {
                            "status": "RUNNING",
                            "wait_reasons": [],
                        },
                    )
                    if updated_task is None:
                        await uow.rollback()
                        continue

                    budget = snapshot.budget
                    budget_updates = {
                        "used_executions":
                            int(budget.used_executions) + 1,
                        "active_executions":
                            int(budget.active_executions) + 1,
                    }
                    if delegated:
                        budget_updates["active_parallel_agents"] = (
                            int(budget.active_parallel_agents) + 1
                        )
                    updated_budget = (
                        await uow.agents.compare_and_set_task_budget(
                            plan.task_id,
                            plan.expected_task_budget_revision,
                            budget_updates,
                        )
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue

                    await uow.agents.save_execution(
                        normalized_execution_values
                    )
                    branch = await uow.agents.compare_and_set_task_branch(
                        plan.branch_id,
                        plan.expected_branch_revision,
                        {"current_execution_id": execution_id},
                    )
                    if branch is None:
                        await uow.rollback()
                        continue

                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": plan.task_id,
                            "kind":
                                TaskBudgetReservationKind.NEW_EXECUTION.value,
                            "reservation_key": execution_id,
                            "payload_fingerprint": execution_fingerprint,
                        }
                    )
                    await uow.agents.save_task_retry_admission(
                        {
                            "task_id": plan.task_id,
                            "retry_request_id": plan.retry_request_id,
                            "plan_fingerprint": plan.plan_fingerprint,
                            "branch_id": plan.branch_id,
                            "source_execution_id":
                                plan.source_execution_id,
                            "source_checkpoint_id":
                                plan.source_checkpoint_id,
                            "execution_id": execution_id,
                            "created_by": plan.target_user_id,
                        }
                    )

                    await uow.commit()
                    return RetryAdmission(
                        task_id=plan.task_id,
                        retry_request_id=plan.retry_request_id,
                        plan_fingerprint=plan.plan_fingerprint,
                        branch_id=plan.branch_id,
                        branch_revision=int(branch.revision),
                        source_execution_id=plan.source_execution_id,
                        source_checkpoint_id=plan.source_checkpoint_id,
                        execution_id=execution_id,
                        execution_revision=1,
                        task_revision=int(updated_task.revision),
                        task_budget_revision=int(updated_budget.revision),
                    )
            except IntegrityError:
                replay = await self._probe_retry_replay(plan)
                if replay is not None:
                    return replay
                continue
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    replay = await self._probe_retry_replay(plan)
                    if replay is not None:
                        return replay
                    continue
                raise

        replay = await self._probe_retry_replay(plan)
        if replay is not None:
            return replay
        raise RetryConsumeConflict(
            "RETRY_CONSUME_CONFLICT",
            f"Atomic RETRY consume conflicts exhausted for {plan.task_id}.",
        )

    @staticmethod
    def _fork_consume_error(exc: Exception) -> ForkConsumeError:
        from .fork_planning import ForkPlanDeferred

        code = str(getattr(exc, "code", "FORK_CONSUME_CONFLICT"))
        message = str(exc)
        if isinstance(exc, ForkPlanDeferred):
            return ForkConsumeDeferred(code, message)
        if code == "FORK_PLAN_FINGERPRINT_INVALID":
            return ForkConsumeRejected(code, message)
        if any(
            token in code
            for token in (
                "CONFLICT",
                "STALE",
                "CHANGED",
            )
        ):
            return ForkConsumeConflict(code, message)
        return ForkConsumeRejected(code, message)

    async def _replay_fork_admission_in_uow(
        self,
        uow,
        *,
        plan: ForkPlan,
        receipt,
    ) -> ForkAdmission:
        if (
            receipt.plan_fingerprint != plan.plan_fingerprint
            or receipt.source_branch_id != plan.source_branch_id
            or receipt.source_execution_id != plan.source_execution_id
            or receipt.source_checkpoint_id != plan.source_checkpoint_id
            or receipt.created_by != plan.target_user_id
            or receipt.runtime_seed_json != fork_runtime_seed_payload(
                plan.runtime_seed
            )
            or receipt.runtime_seed_fingerprint
            != plan.runtime_seed_fingerprint
        ):
            raise ForkConsumeConflict(
                "FORK_REQUEST_SEMANTIC_CONFLICT",
                "fork_request_id already committed with different semantics.",
            )

        branch = await uow.agents.get_task_branch(receipt.branch_id)
        context = await uow.agents.get_task_branch_context(receipt.branch_id)
        execution = await uow.agents.get_execution(receipt.execution_id)
        branch_reservation = await uow.agents.get_task_budget_reservation(
            plan.task_id,
            TaskBudgetReservationKind.BRANCH.value,
            receipt.branch_id,
        )
        execution_reservation = await uow.agents.get_task_budget_reservation(
            plan.task_id,
            TaskBudgetReservationKind.NEW_EXECUTION.value,
            receipt.execution_id,
        )
        task = await uow.agents.get_task(plan.task_id)
        budget = await uow.agents.get_task_budget(plan.task_id)

        corrupt = (
            branch is None
            or context is None
            or execution is None
            or branch_reservation is None
            or execution_reservation is None
            or task is None
            or budget is None
            or branch.task_id != plan.task_id
            or branch.parent_branch_id != receipt.source_branch_id
            or branch.base_execution_id != receipt.source_execution_id
            or branch.base_checkpoint_id != receipt.source_checkpoint_id
            or execution.task_id != plan.task_id
            or execution.branch_id != receipt.branch_id
            or execution.base_execution_id != receipt.source_execution_id
            or execution.base_checkpoint_id != receipt.source_checkpoint_id
            or execution.retry_of_execution_id is not None
        )
        if corrupt:
            raise ForkConsumeConflict(
                "FORK_ADMISSION_CORRUPT",
                "Committed ForkAdmission no longer matches durable outputs.",
            )

        return ForkAdmission(
            task_id=plan.task_id,
            fork_request_id=plan.fork_request_id,
            plan_fingerprint=plan.plan_fingerprint,
            branch_id=receipt.branch_id,
            branch_revision=int(branch.revision),
            execution_id=receipt.execution_id,
            execution_revision=int(execution.revision),
            task_revision=int(task.revision),
            task_budget_revision=int(budget.revision),
        )

    async def _probe_fork_replay(
        self,
        plan: ForkPlan,
    ) -> ForkAdmission | None:
        async with self._uow_factory() as uow:
            receipt = await uow.agents.get_task_fork_admission(
                plan.task_id,
                plan.fork_request_id,
            )
            if receipt is None:
                await uow.commit()
                return None
            admission = await self._replay_fork_admission_in_uow(
                uow,
                plan=plan,
                receipt=receipt,
            )
            await uow.commit()
            return admission

    async def consume_fork_plan(
        self,
        plan: ForkPlan,
    ) -> ForkAdmission:
        """Atomically consume one R8-C ForkPlan into durable R8-D authority.

        This method creates persistence only. It does not start AgentRuntime,
        expose transport/API state, or seed R8-E branch runtime context.
        """
        from .fork_planning import (
            ForkPlanDeferred,
            ForkPlanRejected,
            revalidate_fork_plan_in_uow,
        )

        if fork_plan_fingerprint(plan) != plan.plan_fingerprint:
            raise ForkConsumeRejected(
                "FORK_PLAN_FINGERPRINT_INVALID",
                "ForkPlan semantic fingerprint no longer matches its payload.",
            )

        branch_id = f"r8_fork_{uuid4().hex}"
        execution_id = f"r8_exec_{uuid4().hex}"
        started_at = datetime.now(timezone.utc)

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    receipt = await uow.agents.get_task_fork_admission(
                        plan.task_id,
                        plan.fork_request_id,
                    )
                    if receipt is not None:
                        admission = await self._replay_fork_admission_in_uow(
                            uow,
                            plan=plan,
                            receipt=receipt,
                        )
                        await uow.commit()
                        return admission

                    try:
                        snapshot = await revalidate_fork_plan_in_uow(
                            uow,
                            plan,
                        )
                    except (ForkPlanRejected, ForkPlanDeferred) as exc:
                        await uow.rollback()
                        replay = await self._probe_fork_replay(plan)
                        if replay is not None:
                            return replay
                        raise self._fork_consume_error(exc) from exc

                    source = snapshot.execution
                    delegated = snapshot.delegated

                    normalized_execution_values = (
                        _normalize_execution_store_values(
                            {
                                "id": execution_id,
                                "session_id": plan.session_id,
                                "agent_id": plan.source_agent_id,
                                "task_id": plan.task_id,
                                "branch_id": branch_id,
                                "parent_execution_id":
                                    source.parent_execution_id,
                                "retry_of_execution_id": None,
                                "base_execution_id":
                                    plan.source_execution_id,
                                "base_checkpoint_id":
                                    plan.source_checkpoint_id,
                                "correlation_id": plan.correlation_id,
                                "state": "RUNNING",
                                "wait_reason": None,
                                "revision": 1,
                                "current_checkpoint_id": None,
                                "bound_client_id": None,
                                "bound_connection_id": None,
                                "remaining_active_budget_seconds":
                                    source.remaining_active_budget_seconds,
                                "wait_expires_at": None,
                                "request": dict(source.request or {}),
                                "result": None,
                                "error": None,
                                "started_at": started_at,
                            }
                        )
                    )
                    reservation_execution_values = to_json_safe(
                        normalized_execution_values,
                        path="task_budget.fork_execution_reservation",
                    )

                    branch_payload = {
                        "fork_request_id": plan.fork_request_id,
                        "plan_fingerprint": plan.plan_fingerprint,
                        "parent_branch_id": plan.source_branch_id,
                        "base_execution_id": plan.source_execution_id,
                        "base_checkpoint_id": plan.source_checkpoint_id,
                    }
                    branch_fingerprint = _reservation_fingerprint(
                        TaskBudgetReservationKind.BRANCH,
                        branch_id,
                        branch_payload,
                    )

                    execution_payload = {
                        "fork_request_id": plan.fork_request_id,
                        "plan_fingerprint": plan.plan_fingerprint,
                        "execution_id": execution_id,
                        "execution_values": reservation_execution_values,
                        "delegated": delegated,
                    }
                    execution_fingerprint = _reservation_fingerprint(
                        TaskBudgetReservationKind.NEW_EXECUTION,
                        execution_id,
                        execution_payload,
                    )

                    # FORK changes the durable branch-head activity graph.
                    # Always advance the Task activity epoch, even when the
                    # visible state remains RUNNING. This is the dialect-
                    # independent serialization backstop for SQLite, where
                    # SELECT FOR UPDATE does not provide row-level fencing.
                    updated_task = await uow.agents.compare_and_set_task(
                        plan.task_id,
                        plan.expected_task_revision,
                        {
                            "status": "RUNNING",
                            "wait_reasons": [],
                        },
                    )
                    if updated_task is None:
                        await uow.rollback()
                        continue
                    task_revision = int(updated_task.revision)

                    budget = snapshot.budget
                    budget_updates = {
                        "active_branches":
                            int(budget.active_branches) + 1,
                        "used_executions":
                            int(budget.used_executions) + 1,
                        "active_executions":
                            int(budget.active_executions) + 1,
                    }
                    if delegated:
                        budget_updates["active_parallel_agents"] = (
                            int(budget.active_parallel_agents) + 1
                        )

                    updated_budget = (
                        await uow.agents.compare_and_set_task_budget(
                            plan.task_id,
                            plan.expected_task_budget_revision,
                            budget_updates,
                        )
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue

                    await uow.agents.save_execution(
                        normalized_execution_values
                    )
                    branch = await uow.agents.save_task_branch(
                        {
                            "branch_id": branch_id,
                            "task_id": plan.task_id,
                            "parent_branch_id": plan.source_branch_id,
                            "base_execution_id":
                                plan.source_execution_id,
                            "base_checkpoint_id":
                                plan.source_checkpoint_id,
                            "current_execution_id": execution_id,
                            "resolution_state": "OPEN",
                            "revision": 0,
                            "created_by": plan.target_user_id,
                            "reason": "R8_FORK",
                        }
                    )
                    await uow.agents.save_task_branch_context(
                        {
                            "branch_id": branch_id,
                            "revision": 0,
                            "overlay_messages": [
                                dict(item)
                                for item in plan.overlay_messages
                            ],
                        }
                    )
                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": plan.task_id,
                            "kind":
                                TaskBudgetReservationKind.BRANCH.value,
                            "reservation_key": branch_id,
                            "payload_fingerprint": branch_fingerprint,
                        }
                    )
                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": plan.task_id,
                            "kind":
                                TaskBudgetReservationKind.NEW_EXECUTION.value,
                            "reservation_key": execution_id,
                            "payload_fingerprint": execution_fingerprint,
                        }
                    )
                    await uow.agents.save_task_fork_admission(
                        {
                            "task_id": plan.task_id,
                            "fork_request_id": plan.fork_request_id,
                            "plan_fingerprint":
                                plan.plan_fingerprint,
                            "runtime_seed_json": fork_runtime_seed_payload(
                                plan.runtime_seed
                            ),
                            "runtime_seed_fingerprint":
                                plan.runtime_seed_fingerprint,
                            "source_branch_id":
                                plan.source_branch_id,
                            "source_execution_id":
                                plan.source_execution_id,
                            "source_checkpoint_id":
                                plan.source_checkpoint_id,
                            "branch_id": branch_id,
                            "execution_id": execution_id,
                            "created_by": plan.target_user_id,
                        }
                    )

                    await uow.commit()
                    return ForkAdmission(
                        task_id=plan.task_id,
                        fork_request_id=plan.fork_request_id,
                        plan_fingerprint=plan.plan_fingerprint,
                        branch_id=branch_id,
                        branch_revision=int(branch.revision),
                        execution_id=execution_id,
                        execution_revision=1,
                        task_revision=task_revision,
                        task_budget_revision=int(updated_budget.revision),
                    )
            except IntegrityError:
                replay = await self._probe_fork_replay(plan)
                if replay is not None:
                    return replay
                continue
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    replay = await self._probe_fork_replay(plan)
                    if replay is not None:
                        return replay
                    continue
                raise

        replay = await self._probe_fork_replay(plan)
        if replay is not None:
            return replay
        raise ForkConsumeConflict(
            "FORK_CONSUME_CONFLICT",
            f"Atomic FORK consume conflicts exhausted for {plan.task_id}.",
        )

    async def start_root_task_scoped_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        execution_values: dict[str, Any],
    ) -> RootExecutionAdmission:
        """Atomically admit the one first root Branch + RUNNING execution.

        This is deliberately narrower than general Branch/FORK admission.
        It is valid only for a pristine Task that won the R5-E RUNNING Task
        transition and has no prior TaskBranch or execution history.
        """
        if execution_values.get("id") != execution_id:
            raise ValueError("execution_values.id must match execution_id")
        if execution_values.get("task_id") != task_id:
            raise ValueError("execution_values.task_id must match task_id")
        if execution_values.get("state") != "RUNNING":
            raise ValueError(
                "root execution admission must insert RUNNING state"
            )
        if execution_values.get("revision") != 1:
            raise ValueError(
                "root execution admission must insert revision=1"
            )
        for field in (
            "parent_execution_id",
            "retry_of_execution_id",
            "base_execution_id",
            "base_checkpoint_id",
        ):
            if execution_values.get(field) is not None:
                raise TaskBudgetConflictError(
                    f"first root execution requires {field}=NULL"
                )
        if execution_values.get("branch_id") is not None:
            raise TaskBudgetConflictError(
                "first root execution must not supply branch authority"
            )

        branch_id = _root_branch_id(task_id)
        normalized_execution_values = _normalize_execution_store_values(
            {
                **execution_values,
                "branch_id": branch_id,
            }
        )
        reservation_execution_values = to_json_safe(
            normalized_execution_values,
            path="task_budget.execution_reservation",
        )
        execution_payload = {
            "execution_id": execution_id,
            "execution_values": reservation_execution_values,
            "delegated": False,
            "delegation_depth": 0,
        }
        execution_fingerprint = _reservation_fingerprint(
            TaskBudgetReservationKind.NEW_EXECUTION,
            execution_id,
            execution_payload,
        )
        branch_fingerprint = _reservation_fingerprint(
            TaskBudgetReservationKind.BRANCH,
            branch_id,
            {},
        )

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    existing_branch = await uow.agents.get_task_branch(
                        branch_id
                    )
                    existing_execution = await uow.agents.get_execution(
                        execution_id
                    )
                    existing_branch_reservation = (
                        await uow.agents.get_task_budget_reservation(
                            task_id,
                            TaskBudgetReservationKind.BRANCH.value,
                            branch_id,
                        )
                    )
                    existing_execution_reservation = (
                        await uow.agents.get_task_budget_reservation(
                            task_id,
                            TaskBudgetReservationKind.NEW_EXECUTION.value,
                            execution_id,
                        )
                    )
                    any_existing = any(
                        item is not None
                        for item in (
                            existing_branch,
                            existing_execution,
                            existing_branch_reservation,
                            existing_execution_reservation,
                        )
                    )
                    if any_existing:
                        if (
                            existing_branch is not None
                            and existing_branch.current_execution_id
                            not in (None, execution_id)
                        ):
                            raise TaskBudgetConflictError(
                                "Task already has a normalized TaskBranch; "
                                "second top-level execution is not R8-B root "
                                "admission."
                            )
                        if not all(
                            item is not None
                            for item in (
                                existing_branch,
                                existing_execution,
                                existing_branch_reservation,
                                existing_execution_reservation,
                            )
                        ):
                            raise TaskBudgetConflictError(
                                "Partial root admission state already exists."
                            )
                        self._verify_reservation(
                            existing_branch_reservation,
                            branch_fingerprint,
                        )
                        self._verify_reservation(
                            existing_execution_reservation,
                            execution_fingerprint,
                        )
                        if (
                            existing_branch.task_id != task_id
                            or str(existing_branch.resolution_state) != "OPEN"
                            or existing_branch.current_execution_id
                            != execution_id
                        ):
                            raise TaskBudgetConflictError(
                                "Root TaskBranch belongs to a different "
                                "durable admission."
                            )
                        if (
                            existing_execution.task_id != task_id
                            or existing_execution.branch_id != branch_id
                            or existing_execution.parent_execution_id
                            is not None
                            or int(existing_execution.revision) < 1
                        ):
                            raise TaskBudgetConflictError(
                                "Root AgentExecution does not match the "
                                "durable root admission."
                            )
                        await uow.commit()
                        return RootExecutionAdmission(
                            task_id=task_id,
                            branch_id=branch_id,
                            branch_revision=int(existing_branch.revision),
                            execution_id=execution_id,
                            execution_revision=int(
                                existing_execution.revision
                            ),
                        )

                    task = await uow.agents.get_task(task_id)
                    if task is None:
                        raise TaskBudgetRequiredError(
                            f"Unknown AgentTask: {task_id}"
                        )

                    budget_record = await uow.agents.get_task_budget(task_id)
                    if budget_record is None:
                        if await uow.agents.has_execution_for_task(task_id):
                            raise TaskBudgetLegacyUninitializedError(
                                "Task has durable execution history but no "
                                "TaskBudget."
                            )
                        raise TaskBudgetRequiredError(
                            f"TaskBudget missing: {task_id}"
                        )
                    budget = _budget_from_record(budget_record)
                    self._require_open(budget)

                    if str(task.status) != "RUNNING":
                        raise TaskBudgetConflictError(
                            f"Root execution admission requires RUNNING Task, "
                            f"got {task.status}"
                        )

                    branches = await uow.agents.list_task_branches(task_id)
                    if branches:
                        raise TaskBudgetConflictError(
                            "Task already has a normalized TaskBranch; "
                            "second top-level execution is not R8-B root "
                            "admission."
                        )
                    if await uow.agents.has_execution_for_task(task_id):
                        raise TaskBudgetConflictError(
                            "Task already has AgentExecution history; "
                            "first-root admission is no longer valid."
                        )

                    if (
                        budget.active_branches
                        >= budget.limits.max_active_branches
                    ):
                        raise TaskBudgetExceededError(
                            "max_active_branches reached"
                        )
                    if (
                        budget.used_executions
                        >= budget.limits.max_total_executions
                    ):
                        raise TaskBudgetExceededError(
                            "max_total_executions reached"
                        )
                    if (
                        budget.active_executions
                        >= budget.limits.max_active_executions
                    ):
                        raise TaskBudgetExceededError(
                            "max_active_executions reached"
                        )

                    updated_budget = (
                        await uow.agents.compare_and_set_task_budget(
                            task_id,
                            budget.revision,
                            {
                                "active_branches":
                                    budget.active_branches + 1,
                                "used_executions":
                                    budget.used_executions + 1,
                                "active_executions":
                                    budget.active_executions + 1,
                            },
                        )
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue

                    await uow.agents.save_execution(
                        normalized_execution_values
                    )
                    branch = await uow.agents.save_task_branch(
                        {
                            "branch_id": branch_id,
                            "task_id": task_id,
                            "parent_branch_id": None,
                            "base_execution_id": None,
                            "base_checkpoint_id": None,
                            "current_execution_id": execution_id,
                            "resolution_state": "OPEN",
                            "revision": 0,
                            "created_by": str(task.created_by),
                            "reason": "R8_ROOT_FIRST_EXECUTION",
                        }
                    )
                    await uow.agents.save_task_branch_context(
                        {
                            "branch_id": branch_id,
                            "revision": 0,
                            "overlay_messages": [],
                        }
                    )
                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": task_id,
                            "kind": TaskBudgetReservationKind.BRANCH.value,
                            "reservation_key": branch_id,
                            "payload_fingerprint": branch_fingerprint,
                        }
                    )
                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": task_id,
                            "kind":
                                TaskBudgetReservationKind.NEW_EXECUTION.value,
                            "reservation_key": execution_id,
                            "payload_fingerprint": execution_fingerprint,
                        }
                    )
                    await uow.commit()
                    return RootExecutionAdmission(
                        task_id=task_id,
                        branch_id=branch_id,
                        branch_revision=int(branch.revision),
                        execution_id=execution_id,
                        execution_revision=1,
                    )
            except IntegrityError:
                # Reservation/branch uniqueness is the durable race fence.
                # Re-read on the next iteration to distinguish idempotent
                # same-execution replay from a competing root winner.
                continue
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"Root execution admission conflicts exhausted for {task_id}"
        )

    async def start_task_scoped_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        execution_values: dict[str, Any],
        delegation_depth: int = 0,
    ) -> int:
        """Atomically admit one task-scoped RUNNING execution.

        Top-level pristine executions are routed through the R8-B root
        admission transaction. Delegated child executions stay in the same
        normalized Branch and consume only NEW_EXECUTION capacity.
        """
        if execution_values.get("state") != "RUNNING":
            raise ValueError(
                "task-scoped execution admission must insert RUNNING state"
            )
        if execution_values.get("revision") != 1:
            raise ValueError(
                "task-scoped execution admission must insert revision=1"
            )
        if execution_values.get("parent_execution_id") is None:
            if execution_values.get("branch_id") is not None:
                raise TaskBudgetConflictError(
                    "new top-level execution on an existing branch is outside "
                    "R8-B; retry/fork authority is not implemented"
                )
            admission = await self.start_root_task_scoped_execution(
                task_id,
                execution_id=execution_id,
                execution_values=execution_values,
            )
            return admission.execution_revision

        await self.reserve_new_execution(
            task_id,
            execution_id=execution_id,
            execution_values=execution_values,
            delegation_depth=delegation_depth,
        )
        return 1

    async def resume_task_scoped_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        source_revision: int,
        transition_values: dict[str, Any],
        delegated: bool,
    ) -> int:
        """Atomically WAITING -> RUNNING and reacquire active capacity."""
        if transition_values.get("state") != "RUNNING":
            raise ValueError("resume transition must target RUNNING")

        def mutate(budget: TaskBudget) -> dict[str, Any]:
            self._require_open(budget)
            if budget.active_executions >= budget.limits.max_active_executions:
                raise TaskBudgetExceededError("max_active_executions reached")
            if (
                delegated
                and budget.active_parallel_agents
                >= budget.limits.max_parallel_agents
            ):
                raise TaskBudgetExceededError("max_parallel_agents reached")
            return {
                "active_executions": budget.active_executions + 1,
                "active_parallel_agents": (
                    budget.active_parallel_agents + 1
                    if delegated
                    else budget.active_parallel_agents
                ),
            }

        return await self._transition_execution_with_budget(
            task_id=task_id,
            execution_id=execution_id,
            source_revision=source_revision,
            expected_state="WAITING",
            transition_values=transition_values,
            kind=TaskBudgetReservationKind.RESUME_EXECUTION,
            reservation_key=f"{execution_id}:{source_revision}",
            reservation_payload={
                "execution_id": execution_id,
                "source_revision": source_revision,
                "delegated": delegated,
            },
            mutate_budget=mutate,
        )

    async def expire_task_scoped_waiting_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        source_revision: int,
        transition_values: dict[str, Any],
    ) -> int | None:
        """Atomically WAITING -> TIMEOUT with R8 multi-branch activity.

        WAITING already released active execution capacity, so this transition
        is deliberately TaskBudget-accounting neutral. The Task row is the
        serialization authority and the execution timeout plus aggregate Task
        projection commit together.
        """

        if str(transition_values.get("state") or "") != "TIMEOUT":
            raise ValueError("waiting expiry transition must target TIMEOUT")
        normalized = _normalize_execution_store_values(transition_values)

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    task = await uow.agents.get_task_for_update(task_id)
                    if task is None:
                        raise TaskBudgetRequiredError(
                            f"Unknown AgentTask: {task_id}"
                        )

                    execution = await uow.agents.get_execution(execution_id)
                    if execution is None:
                        raise TaskBudgetConflictError(
                            f"Unknown AgentExecution: {execution_id}"
                        )
                    if execution.task_id != task_id:
                        raise TaskBudgetConflictError(
                            "AgentExecution belongs to a different AgentTask."
                        )
                    if (
                        str(execution.state) != "WAITING"
                        or int(execution.revision) != int(source_revision)
                    ):
                        await uow.commit()
                        return None

                    updated = await uow.agents.compare_and_set_execution(
                        execution_id,
                        source_revision,
                        normalized,
                    )
                    if updated is None:
                        await uow.rollback()
                        return None

                    activity = await reconcile_multibranch_task_activity_in_uow(
                        uow,
                        task_id=task_id,
                        locked_task=task,
                    )
                    if activity is None:
                        # The timeout mutation is part of this same UoW, so an
                        # activity race rolls it back before retry.
                        await uow.rollback()
                        continue

                    await uow.commit()
                    return source_revision + 1
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            "Task-scoped WAITING expiry activity conflicts exhausted for "
            f"{task_id}/{execution_id}@{source_revision}"
        )

    async def finish_task_scoped_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        source_revision: int,
        transition_values: dict[str, Any],
        delegated: bool,
        checkpoint_values: dict[str, Any] | None = None,
        pending_invocations: Sequence[dict[str, Any]] = (),
    ) -> int:
        """Atomically release one RUNNING execution's active capacity."""
        target_state = str(transition_values.get("state") or "")
        if target_state not in {
            "WAITING",
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "TIMEOUT",
        }:
            raise ValueError(
                "finish transition must target WAITING or a terminal state"
            )

        target_revision = source_revision + 1
        if target_state != "WAITING" and checkpoint_values is not None:
            raise ValueError(
                "checkpoint_values are only valid for WAITING transitions"
            )

        def mutate(budget: TaskBudget) -> dict[str, Any]:
            if budget.active_executions <= 0:
                raise TaskBudgetConflictError(
                    "active_executions is already zero"
                )
            if delegated and budget.active_parallel_agents <= 0:
                raise TaskBudgetConflictError(
                    "active_parallel_agents is already zero"
                )
            return {
                "active_executions": budget.active_executions - 1,
                "active_parallel_agents": (
                    budget.active_parallel_agents - 1
                    if delegated
                    else budget.active_parallel_agents
                ),
            }

        return await self._transition_execution_with_budget(
            task_id=task_id,
            execution_id=execution_id,
            source_revision=source_revision,
            expected_state="RUNNING",
            transition_values=transition_values,
            kind=TaskBudgetReservationKind.RELEASE_EXECUTION,
            reservation_key=f"{execution_id}:{target_revision}",
            reservation_payload={
                "execution_id": execution_id,
                "target_revision": target_revision,
                "target_state": target_state,
                "delegated": delegated,
                "checkpoint_id": (
                    str(checkpoint_values.get("checkpoint_id"))
                    if checkpoint_values is not None
                    else None
                ),
                "pending_invocations": [
                    {
                        "ordinal": int(item["ordinal"]),
                        "invocation_id": str(item["invocation_id"]),
                        "tool_call_id": str(item["tool_call_id"]),
                        "capability_id": str(item["capability_id"]),
                    }
                    for item in pending_invocations
                ],
            },
            mutate_budget=mutate,
            checkpoint_values=checkpoint_values,
            pending_invocations=pending_invocations,
        )

    async def reserve_tool_call_batch(
        self,
        task_id: str,
        calls: Sequence[dict[str, Any]],
    ) -> TaskBudget:
        """Atomically reserve all not-yet-reserved logical tool calls.

        Each tool_call_id keeps its own durable reservation identity. Replays
        after WAITING/resume therefore do not consume additional task budget.
        """
        normalized: list[tuple[str, dict[str, Any], str]] = []
        seen: dict[str, str] = {}
        for raw in calls:
            payload = to_json_safe(
                dict(raw),
                path="task_budget.tool_call",
            )
            key = str(payload.get("tool_call_id") or "")
            if not key:
                raise ValueError("tool_call_id must be non-empty")
            fingerprint = _reservation_fingerprint(
                TaskBudgetReservationKind.TOOL_CALL,
                key,
                payload,
            )
            prior = seen.get(key)
            if prior is not None:
                if prior != fingerprint:
                    raise TaskBudgetConflictError(
                        "tool_call_id was reused with a different payload"
                    )
                continue
            seen[key] = fingerprint
            normalized.append((key, payload, fingerprint))

        if not normalized:
            raise ValueError("calls must not be empty")

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    budget_record = await uow.agents.get_task_budget(task_id)
                    if budget_record is None:
                        if await uow.agents.has_execution_for_task(task_id):
                            raise TaskBudgetLegacyUninitializedError(
                                "Task has durable execution history but no "
                                "TaskBudget."
                            )
                        raise TaskBudgetRequiredError(
                            f"TaskBudget missing: {task_id}"
                        )
                    budget = _budget_from_record(budget_record)
                    missing: list[tuple[str, dict[str, Any], str]] = []

                    for key, payload, fingerprint in normalized:
                        existing = (
                            await uow.agents.get_task_budget_reservation(
                                task_id,
                                TaskBudgetReservationKind.TOOL_CALL.value,
                                key,
                            )
                        )
                        if existing is None:
                            missing.append((key, payload, fingerprint))
                        else:
                            self._verify_reservation(existing, fingerprint)

                    if not missing:
                        await uow.commit()
                        return budget

                    self._require_open(budget)
                    proposed = budget.used_tool_calls + len(missing)
                    if proposed > budget.limits.max_total_tool_calls:
                        raise TaskBudgetExceededError(
                            "max_total_tool_calls exceeded"
                        )

                    updated = await uow.agents.compare_and_set_task_budget(
                        task_id,
                        budget.revision,
                        {"used_tool_calls": proposed},
                    )
                    if updated is None:
                        await uow.rollback()
                        continue

                    for key, _payload, fingerprint in missing:
                        await uow.agents.save_task_budget_reservation(
                            {
                                "task_id": task_id,
                                "kind": (
                                    TaskBudgetReservationKind.TOOL_CALL.value
                                ),
                                "reservation_key": key,
                                "payload_fingerprint": fingerprint,
                            }
                        )
                    result = _budget_from_record(updated)
                    await uow.commit()
                    return result
            except IntegrityError:
                # Another writer may have committed one or more logical call
                # reservations first. Re-read all keys in a fresh UoW.
                continue
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"Tool-call reservation conflicts exhausted for {task_id}"
        )

    async def reconcile_multibranch_task_activity(
        self,
        task_id: str,
    ):
        """Retry/rederive the minimal R8 multi-branch Task activity projection."""

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    task = await uow.agents.get_task_for_update(task_id)
                    if task is None:
                        raise TaskBudgetRequiredError(
                            f"Unknown AgentTask: {task_id}"
                        )
                    updated = await reconcile_multibranch_task_activity_in_uow(
                        uow,
                        task_id=task_id,
                        locked_task=task,
                    )
                    if updated is None:
                        await uow.rollback()
                        continue
                    await uow.commit()
                    return updated
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"Task activity CAS conflicts exhausted for {task_id}"
        )

    async def get_budget(self, task_id: str) -> TaskBudget | None:
        async with self._uow_factory() as uow:
            record = await uow.agents.get_task_budget(task_id)
            if record is None:
                await uow.commit()
                return None
            budget = _budget_from_record(record)
            await uow.commit()
            return budget

    async def ensure_budget(
        self,
        task_id: str,
        limits: TaskBudgetLimits,
        policy: TaskBudgetPolicy | None = None,
    ) -> TaskBudget:
        policy = policy or TaskBudgetPolicy()
        fingerprint = task_budget_policy_fingerprint(limits, policy)

        try:
            async with self._uow_factory() as uow:
                existing = await uow.agents.get_task_budget(task_id)
                if existing is not None:
                    budget = _budget_from_record(existing)
                    self._require_same_policy(
                        budget,
                        policy,
                        fingerprint,
                    )
                    await uow.commit()
                    return budget

                task = await uow.agents.get_task(task_id)
                if task is None:
                    raise TaskBudgetRequiredError(
                        f"Unknown AgentTask: {task_id}"
                    )
                if str(task.status) in {"COMPLETED", "CANCELLED"}:
                    raise TaskBudgetClosedError(
                        f"Terminal AgentTask cannot initialize budget: {task_id}"
                    )
                if await uow.agents.has_execution_for_task(task_id):
                    raise TaskBudgetLegacyUninitializedError(
                        "Task has durable execution history but no TaskBudget; "
                        "usage cannot be reconstructed safely."
                    )

                record = await uow.agents.save_task_budget(
                    self._new_budget_values(
                        task_id,
                        limits,
                        policy,
                        fingerprint,
                    )
                )
                budget = _budget_from_record(record)
                await uow.commit()
                return budget
        except IntegrityError:
            # Concurrent pristine initialization: reload the winner and verify
            # that the immutable policy is identical.
            async with self._uow_factory() as uow:
                existing = await uow.agents.get_task_budget(task_id)
                if existing is None:
                    raise TaskBudgetConflictError(
                        f"Concurrent TaskBudget initialization lost: {task_id}"
                    )
                budget = _budget_from_record(existing)
                self._require_same_policy(
                    budget,
                    policy,
                    fingerprint,
                )
                await uow.commit()
                return budget

    async def reserve_new_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        execution_values: dict[str, Any],
        delegation_depth: int = 0,
    ) -> TaskBudget:
        if delegation_depth < 0:
            raise ValueError("delegation_depth must be non-negative")
        if execution_values.get("id") != execution_id:
            raise ValueError("execution_values.id must match execution_id")
        if execution_values.get("task_id") != task_id:
            raise ValueError("execution_values.task_id must match task_id")
        normalized_execution_values = _normalize_execution_store_values(
            execution_values
        )
        reservation_execution_values = to_json_safe(
            normalized_execution_values,
            path="task_budget.execution_reservation",
        )
        delegated = (
            normalized_execution_values.get("parent_execution_id")
            is not None
        )

        def mutate(budget: TaskBudget) -> dict[str, Any]:
            self._require_open(budget)
            if delegation_depth > budget.limits.max_delegation_depth:
                raise DelegationDepthExceededError(
                    f"delegation_depth={delegation_depth} exceeds "
                    f"{budget.limits.max_delegation_depth}"
                )
            if budget.used_executions >= budget.limits.max_total_executions:
                raise TaskBudgetExceededError("max_total_executions reached")
            if budget.active_executions >= budget.limits.max_active_executions:
                raise TaskBudgetExceededError("max_active_executions reached")
            if (
                delegated
                and budget.active_parallel_agents
                >= budget.limits.max_parallel_agents
            ):
                raise TaskBudgetExceededError("max_parallel_agents reached")
            return {
                "used_executions": budget.used_executions + 1,
                "active_executions": budget.active_executions + 1,
                "active_parallel_agents": (
                    budget.active_parallel_agents + 1
                    if delegated
                    else budget.active_parallel_agents
                ),
            }

        async def validate_delegated_branch(uow) -> None:
            if not delegated:
                return
            branch_id = normalized_execution_values.get("branch_id")
            parent_execution_id = normalized_execution_values.get(
                "parent_execution_id"
            )
            if not branch_id:
                raise TaskBudgetConflictError(
                    "Delegated execution requires durable branch_id."
                )
            branch = await uow.agents.get_task_branch(str(branch_id))
            if (
                branch is None
                or branch.task_id != task_id
                or str(branch.resolution_state) != "OPEN"
            ):
                raise TaskBudgetConflictError(
                    "Delegated execution requires an OPEN TaskBranch owned "
                    "by the same AgentTask."
                )
            parent = await uow.agents.get_execution(
                str(parent_execution_id)
            )
            if (
                parent is None
                or parent.task_id != task_id
                or parent.branch_id != branch_id
            ):
                raise TaskBudgetConflictError(
                    "Delegated execution parent must belong to the same "
                    "TaskBranch."
                )

        async def side_effect(uow):
            await validate_delegated_branch(uow)
            existing = await uow.agents.get_execution(execution_id)
            if existing is not None:
                raise TaskBudgetConflictError(
                    "Execution exists without matching TaskBudget reservation."
                )
            await uow.agents.save_execution(normalized_execution_values)

        async def verify_idempotent(uow):
            await validate_delegated_branch(uow)
            existing = await uow.agents.get_execution(execution_id)
            if (
                existing is None
                or existing.task_id != task_id
                or (
                    delegated
                    and existing.branch_id
                    != normalized_execution_values.get("branch_id")
                )
            ):
                raise TaskBudgetConflictError(
                    "Idempotent execution reservation has no matching execution."
                )

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.NEW_EXECUTION,
            execution_id,
            {
                "execution_id": execution_id,
                "execution_values": reservation_execution_values,
                "delegated": delegated,
                "delegation_depth": delegation_depth,
            },
            mutate,
            side_effect=side_effect,
            verify_idempotent=verify_idempotent,
        )

    async def reserve_resume_slot(
        self,
        task_id: str,
        *,
        execution_id: str,
        source_revision: int,
        delegated: bool,
    ) -> TaskBudget:
        if source_revision < 0:
            raise ValueError("source_revision must be non-negative")
        key = f"{execution_id}:{source_revision}"

        def mutate(budget: TaskBudget) -> dict[str, Any]:
            self._require_open(budget)
            if budget.active_executions >= budget.limits.max_active_executions:
                raise TaskBudgetExceededError("max_active_executions reached")
            if (
                delegated
                and budget.active_parallel_agents
                >= budget.limits.max_parallel_agents
            ):
                raise TaskBudgetExceededError("max_parallel_agents reached")
            return {
                "active_executions": budget.active_executions + 1,
                "active_parallel_agents": (
                    budget.active_parallel_agents + 1
                    if delegated
                    else budget.active_parallel_agents
                ),
            }

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.RESUME_EXECUTION,
            key,
            {"execution_id": execution_id, "delegated": delegated},
            mutate,
        )

    async def release_active_execution(
        self,
        task_id: str,
        *,
        execution_id: str,
        target_revision: int,
        delegated: bool,
    ) -> TaskBudget:
        if target_revision < 0:
            raise ValueError("target_revision must be non-negative")
        key = f"{execution_id}:{target_revision}"

        def mutate(budget: TaskBudget) -> dict[str, Any]:
            if budget.active_executions <= 0:
                raise TaskBudgetConflictError(
                    "active_executions is already zero"
                )
            if delegated and budget.active_parallel_agents <= 0:
                raise TaskBudgetConflictError(
                    "active_parallel_agents is already zero"
                )
            return {
                "active_executions": budget.active_executions - 1,
                "active_parallel_agents": (
                    budget.active_parallel_agents - 1
                    if delegated
                    else budget.active_parallel_agents
                ),
            }

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.RELEASE_EXECUTION,
            key,
            {"execution_id": execution_id, "delegated": delegated},
            mutate,
        )

    async def reserve_tool_calls(
        self,
        task_id: str,
        *,
        reservation_key: str,
        count: int,
    ) -> TaskBudget:
        if count <= 0:
            raise ValueError("count must be positive")

        def mutate(budget: TaskBudget) -> dict[str, Any]:
            self._require_open(budget)
            proposed = budget.used_tool_calls + count
            if proposed > budget.limits.max_total_tool_calls:
                raise TaskBudgetExceededError("max_total_tool_calls exceeded")
            return {"used_tool_calls": proposed}

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.TOOL_CALL,
            reservation_key,
            {"count": count},
            mutate,
        )

    async def reserve_inference(
        self,
        task_id: str,
        *,
        request_id: str,
    ) -> TaskBudget:
        def mutate(budget: TaskBudget) -> dict[str, Any]:
            self._require_open(budget)
            if (
                budget.used_inference_calls
                >= budget.limits.max_total_inference_calls
            ):
                raise TaskBudgetExceededError(
                    "max_total_inference_calls reached"
                )
            if (
                budget.limits.max_total_tokens is not None
                and budget.used_tokens >= budget.limits.max_total_tokens
            ):
                raise TaskBudgetExceededError("max_total_tokens reached")
            if (
                budget.limits.max_total_cost_usd is not None
                and budget.used_cost_usd
                >= budget.limits.max_total_cost_usd
            ):
                raise TaskBudgetExceededError("max_total_cost_usd reached")
            return {
                "used_inference_calls": budget.used_inference_calls + 1
            }

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.INFERENCE,
            request_id,
            {},
            mutate,
        )

    async def account_usage(
        self,
        task_id: str,
        *,
        usage_key: str,
        tokens: int = 0,
        cost_usd: Decimal | int | float | str = Decimal("0"),
    ) -> TaskBudget:
        if tokens < 0:
            raise ValueError("tokens must be non-negative")
        normalized_cost = normalize_task_budget_cost(cost_usd)
        if normalized_cost < 0:
            raise ValueError("cost_usd must be non-negative")

        def mutate(budget: TaskBudget) -> dict[str, Any]:
            return {
                "used_tokens": budget.used_tokens + tokens,
                "used_cost_usd": normalize_task_budget_cost(
                    budget.used_cost_usd + normalized_cost
                ),
            }

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.USAGE,
            usage_key,
            {
                "tokens": tokens,
                "cost_usd": str(normalized_cost),
            },
            mutate,
        )

    async def reserve_branch_slot(
        self,
        task_id: str,
        *,
        reservation_key: str,
    ) -> TaskBudget:
        def mutate(budget: TaskBudget) -> dict[str, Any]:
            self._require_open(budget)
            if budget.active_branches >= budget.limits.max_active_branches:
                raise TaskBudgetExceededError("max_active_branches reached")
            return {"active_branches": budget.active_branches + 1}

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.BRANCH,
            reservation_key,
            {},
            mutate,
        )

    async def release_branch_slot(
        self,
        task_id: str,
        *,
        reservation_key: str,
    ) -> TaskBudget:
        def mutate(budget: TaskBudget) -> dict[str, Any]:
            if budget.active_branches <= 0:
                raise TaskBudgetConflictError(
                    "active_branches is already zero"
                )
            return {"active_branches": budget.active_branches - 1}

        return await self._mutate_with_reservation(
            task_id,
            TaskBudgetReservationKind.RELEASE_BRANCH,
            reservation_key,
            {},
            mutate,
        )

    async def close(self, task_id: str) -> TaskBudget:
        for _ in range(self._max_conflict_retries):
            async with self._uow_factory() as uow:
                record = await uow.agents.get_task_budget(task_id)
                if record is None:
                    raise TaskBudgetRequiredError(
                        f"TaskBudget missing: {task_id}"
                    )
                budget = _budget_from_record(record)
                if budget.state is TaskBudgetState.CLOSED:
                    await uow.commit()
                    return budget
                updated = await uow.agents.compare_and_set_task_budget(
                    task_id,
                    budget.revision,
                    {
                        "state": TaskBudgetState.CLOSED.value,
                        "closed_at": datetime.now(timezone.utc),
                    },
                )
                if updated is None:
                    await uow.rollback()
                    continue
                result = _budget_from_record(updated)
                await uow.commit()
                return result
        raise TaskBudgetConflictError(
            f"Could not close TaskBudget after CAS conflicts: {task_id}"
        )

    async def _mutate_with_reservation(
        self,
        task_id: str,
        kind: TaskBudgetReservationKind,
        reservation_key: str,
        payload: dict[str, Any],
        mutate: Callable[[TaskBudget], dict[str, Any]],
        *,
        side_effect=None,
        verify_idempotent=None,
    ) -> TaskBudget:
        if not reservation_key:
            raise ValueError("reservation_key must be non-empty")
        fingerprint = _reservation_fingerprint(
            kind,
            reservation_key,
            payload,
        )

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    existing = (
                        await uow.agents.get_task_budget_reservation(
                            task_id,
                            kind.value,
                            reservation_key,
                        )
                    )
                    if existing is not None:
                        self._verify_reservation(existing, fingerprint)
                        if verify_idempotent is not None:
                            await verify_idempotent(uow)
                        budget_record = await uow.agents.get_task_budget(
                            task_id
                        )
                        if budget_record is None:
                            raise TaskBudgetRequiredError(
                                f"TaskBudget missing: {task_id}"
                            )
                        budget = _budget_from_record(budget_record)
                        await uow.commit()
                        return budget

                    budget_record = await uow.agents.get_task_budget(task_id)
                    if budget_record is None:
                        if await uow.agents.has_execution_for_task(task_id):
                            raise TaskBudgetLegacyUninitializedError(
                                "Task has durable execution history but no "
                                "TaskBudget."
                            )
                        raise TaskBudgetRequiredError(
                            f"TaskBudget missing: {task_id}"
                        )
                    budget = _budget_from_record(budget_record)
                    values = mutate(budget)

                    updated = (
                        await uow.agents.compare_and_set_task_budget(
                            task_id,
                            budget.revision,
                            values,
                        )
                    )
                    if updated is None:
                        await uow.rollback()
                        continue

                    if side_effect is not None:
                        await side_effect(uow)

                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": task_id,
                            "kind": kind.value,
                            "reservation_key": reservation_key,
                            "payload_fingerprint": fingerprint,
                        }
                    )
                    result = _budget_from_record(updated)
                    await uow.commit()
                    return result
            except IntegrityError:
                recovered = await self._load_idempotent(
                    task_id,
                    kind,
                    reservation_key,
                    fingerprint,
                    verify_idempotent=verify_idempotent,
                )
                if recovered is not None:
                    return recovered
                raise
            except OperationalError as exc:
                # SQLite may surface a concurrent writer as a transient lock.
                # Only retry that known contention signal; never hide a real
                # SQL/schema/connection failure as a TaskBudget conflict.
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"TaskBudget CAS conflicts exhausted for {task_id}"
        )

    async def _transition_execution_with_budget(
        self,
        *,
        task_id: str,
        execution_id: str,
        source_revision: int,
        expected_state: str,
        transition_values: dict[str, Any],
        kind: TaskBudgetReservationKind,
        reservation_key: str,
        reservation_payload: dict[str, Any],
        mutate_budget: Callable[[TaskBudget], dict[str, Any]],
        checkpoint_values: dict[str, Any] | None = None,
        pending_invocations: Sequence[dict[str, Any]] = (),
    ) -> int:
        if source_revision < 0:
            raise ValueError("source_revision must be non-negative")
        target_revision = source_revision + 1
        normalized_values = _normalize_execution_store_values(
            transition_values
        )
        # The durable transition key/revision/state are the semantic
        # idempotency identity. Volatile timestamps/result payload fields are
        # intentionally excluded so a retry after an uncertain commit can
        # observe the first committed transition instead of conflicting on
        # a newly generated timestamp.
        fingerprint = _reservation_fingerprint(
            kind,
            reservation_key,
            reservation_payload,
        )

        for _ in range(self._max_conflict_retries):
            try:
                async with self._uow_factory() as uow:
                    existing_reservation = (
                        await uow.agents.get_task_budget_reservation(
                            task_id,
                            kind.value,
                            reservation_key,
                        )
                    )
                    if existing_reservation is not None:
                        self._verify_reservation(
                            existing_reservation,
                            fingerprint,
                        )
                        execution = await uow.agents.get_execution(
                            execution_id
                        )
                        if (
                            execution is None
                            or execution.task_id != task_id
                            or execution.revision < target_revision
                        ):
                            raise TaskBudgetConflictError(
                                "Durable transition reservation has no "
                                "matching AgentExecution."
                            )
                        if checkpoint_values is not None:
                            await verify_committed_waiting_checkpoint(
                                uow,
                                execution=execution,
                                source_revision=source_revision,
                                checkpoint_values=checkpoint_values,
                                pending_invocations=pending_invocations,
                            )
                        await uow.commit()
                        return target_revision

                    execution = await uow.agents.get_execution(execution_id)
                    if execution is None or execution.task_id != task_id:
                        raise TaskBudgetConflictError(
                            f"Unknown task-scoped execution: {execution_id}"
                        )
                    if execution.revision != source_revision:
                        raise TaskBudgetConflictError(
                            "Stale AgentExecution revision: "
                            f"{execution_id}@{source_revision}"
                        )
                    if str(execution.state) != expected_state:
                        raise TaskBudgetConflictError(
                            f"AgentExecution {execution_id} is "
                            f"{execution.state}, expected {expected_state}"
                        )

                    budget_record = await uow.agents.get_task_budget(task_id)
                    if budget_record is None:
                        if await uow.agents.has_execution_for_task(task_id):
                            raise TaskBudgetLegacyUninitializedError(
                                "Task has durable execution history but no "
                                "TaskBudget."
                            )
                        raise TaskBudgetRequiredError(
                            f"TaskBudget missing: {task_id}"
                        )
                    budget = _budget_from_record(budget_record)
                    budget_values = mutate_budget(budget)

                    updated_budget = (
                        await uow.agents.compare_and_set_task_budget(
                            task_id,
                            budget.revision,
                            budget_values,
                        )
                    )
                    if updated_budget is None:
                        await uow.rollback()
                        continue

                    execution_values = normalized_values
                    if checkpoint_values is not None:
                        execution_values = await stage_waiting_checkpoint(
                            uow,
                            execution=execution,
                            source_revision=source_revision,
                            transition_values=normalized_values,
                            checkpoint_values=checkpoint_values,
                            pending_invocations=pending_invocations,
                        )

                    updated_execution = (
                        await uow.agents.compare_and_set_execution(
                            execution_id,
                            source_revision,
                            execution_values,
                        )
                    )
                    if updated_execution is None:
                        await uow.rollback()
                        raise TaskBudgetConflictError(
                            "AgentExecution CAS lost during TaskBudget "
                            "transition."
                        )

                    await uow.agents.save_task_budget_reservation(
                        {
                            "task_id": task_id,
                            "kind": kind.value,
                            "reservation_key": reservation_key,
                            "payload_fingerprint": fingerprint,
                        }
                    )
                    await uow.commit()
                    return target_revision
            except IntegrityError:
                # Reservation uniqueness is the durable idempotency fence.
                # A concurrent winner is observed on the next iteration.
                continue
            except OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    continue
                raise

        raise TaskBudgetConflictError(
            f"Execution/TaskBudget transition conflicts exhausted for "
            f"{execution_id}"
        )

    async def _load_idempotent(
        self,
        task_id: str,
        kind: TaskBudgetReservationKind,
        reservation_key: str,
        fingerprint: str,
        *,
        verify_idempotent=None,
    ) -> TaskBudget | None:
        async with self._uow_factory() as uow:
            existing = await uow.agents.get_task_budget_reservation(
                task_id,
                kind.value,
                reservation_key,
            )
            if existing is None:
                await uow.commit()
                return None
            self._verify_reservation(existing, fingerprint)
            if verify_idempotent is not None:
                await verify_idempotent(uow)
            budget_record = await uow.agents.get_task_budget(task_id)
            if budget_record is None:
                raise TaskBudgetRequiredError(
                    f"TaskBudget missing: {task_id}"
                )
            budget = _budget_from_record(budget_record)
            await uow.commit()
            return budget

    @staticmethod
    def _verify_reservation(record, fingerprint: str) -> None:
        if record.payload_fingerprint != fingerprint:
            raise TaskBudgetConflictError(
                "reservation_key was reused with a different payload"
            )

    @staticmethod
    def _require_open(budget: TaskBudget) -> None:
        if budget.state is not TaskBudgetState.OPEN:
            raise TaskBudgetClosedError(
                f"TaskBudget is CLOSED: {budget.task_id}"
            )

    @staticmethod
    def _require_same_policy(
        budget: TaskBudget,
        policy: TaskBudgetPolicy,
        fingerprint: str,
    ) -> None:
        if (
            budget.policy_version != policy.version
            or budget.policy_fingerprint != fingerprint
            or budget.deny_recursive_agent_cycle
            != policy.deny_recursive_agent_cycle
        ):
            raise TaskBudgetConflictError(
                "existing TaskBudget policy/limits are immutable"
            )

    @staticmethod
    def _new_budget_values(
        task_id: str,
        limits: TaskBudgetLimits,
        policy: TaskBudgetPolicy,
        fingerprint: str,
    ) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "revision": 0,
            "state": TaskBudgetState.OPEN.value,
            "max_total_executions": limits.max_total_executions,
            "max_active_executions": limits.max_active_executions,
            "max_active_branches": limits.max_active_branches,
            "max_parallel_agents": limits.max_parallel_agents,
            "max_total_tool_calls": limits.max_total_tool_calls,
            "max_total_inference_calls": limits.max_total_inference_calls,
            "max_total_tokens": limits.max_total_tokens,
            "max_total_cost_usd": limits.max_total_cost_usd,
            "max_delegation_depth": limits.max_delegation_depth,
            "policy_version": policy.version,
            "policy_fingerprint": fingerprint,
            "deny_recursive_agent_cycle": policy.deny_recursive_agent_cycle,
            "used_executions": 0,
            "active_executions": 0,
            "active_branches": 0,
            "active_parallel_agents": 0,
            "used_tool_calls": 0,
            "used_inference_calls": 0,
            "used_tokens": 0,
            "used_cost_usd": Decimal("0"),
            "closed_at": None,
        }


__all__ = [
    "AgentDelegationCycleError",
    "DelegationDepthExceededError",
    "DelegationAdmission",
    "RootExecutionAdmission",
    "ForkConsumeError",
    "ForkConsumeRejected",
    "ForkConsumeDeferred",
    "ForkConsumeConflict",
    "RetryConsumeError",
    "RetryConsumeRejected",
    "RetryConsumeDeferred",
    "RetryConsumeConflict",
    "BranchResolutionError",
    "BranchDiscardResult",
    "TaskAdoptionResult",
    "AggregateAdmission",
    "AggregateAdmissionError",
    "TaskBudgetClosedError",
    "TaskBudgetConflictError",
    "TaskBudgetError",
    "TaskBudgetExceededError",
    "TaskBudgetLegacyUninitializedError",
    "TaskBudgetRequiredError",
    "TaskBudgetService",
    "prepare_resume_capacity_in_uow",
]
