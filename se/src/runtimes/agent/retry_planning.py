from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from ...domain.schemas.agent_execution import AgentExecutionLimits
from .contracts.retry import (
    RetryPlan,
    retry_plan_fingerprint,
    retry_value_fingerprint,
)
from .serialization import to_json_safe


class RetryPlanError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RetryPlanRejected(RetryPlanError):
    """The durable source cannot satisfy this exact RETRY intent."""


class RetryPlanDeferred(RetryPlanError):
    """The source is valid but current TaskBudget capacity blocks RETRY."""


@dataclass(frozen=True, slots=True)
class RetryRevalidationSnapshot:
    task: Any
    budget: Any
    branch: Any
    execution: Any
    checkpoint: Any | None
    delegated: bool
    fresh_active_budget_seconds: float
    runtime_context_state: dict[str, Any]


def _value(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _require_identifier(name: str, value: str) -> None:
    if not str(value or "").strip():
        raise RetryPlanRejected(
            "RETRY_REQUEST_INVALID",
            f"{name} must be non-empty.",
        )


def _fork_seed_context_state(receipt) -> dict[str, Any] | None:
    seed = dict(getattr(receipt, "runtime_seed_json", None) or {})
    if not seed:
        return None
    return {
        "request_id": seed.get("request_id"),
        "workflow_id": seed.get("workflow_id"),
        "metadata": dict(seed.get("metadata") or {}),
        "causation_id": seed.get("causation_id"),
        "trace_id": seed.get("trace_id"),
        "limits": dict(seed.get("limits") or {}),
    }


def _runtime_proof(
    execution,
    context_state: Mapping[str, Any] | None = None,
) -> tuple[str, str, float]:
    request = to_json_safe(
        dict(getattr(execution, "request", None) or {}),
        path="retry_plan.request",
    )
    state = (
        context_state
        if context_state is not None
        else getattr(execution, "context_state", None)
    )
    if not isinstance(state, Mapping):
        raise RetryPlanRejected(
            "RETRY_RUNTIME_CONTEXT_INCOMPLETE",
            "Source execution has no durable context_state mapping.",
        )
    safe_state = to_json_safe(
        dict(state),
        path="retry_plan.source_context_state",
    )
    raw_limits = safe_state.get("limits")
    if not isinstance(raw_limits, Mapping):
        raise RetryPlanRejected(
            "RETRY_RUNTIME_CONTEXT_INCOMPLETE",
            "Source execution context_state has no durable limits.",
        )
    try:
        limits = AgentExecutionLimits.model_validate(dict(raw_limits))
    except Exception as exc:
        raise RetryPlanRejected(
            "RETRY_RUNTIME_CONTEXT_INCOMPLETE",
            "Source execution limits are invalid.",
        ) from exc

    fresh_budget = float(limits.timeout_seconds)
    if not math.isfinite(fresh_budget) or fresh_budget <= 0:
        raise RetryPlanRejected(
            "RETRY_RUNTIME_CONTEXT_INCOMPLETE",
            "Source execution timeout budget is invalid.",
        )
    return (
        retry_value_fingerprint(request),
        retry_value_fingerprint(safe_state),
        fresh_budget,
    )


async def _require_no_source_capability_side_effects(
    store,
    execution_id: str,
) -> None:
    loader = getattr(store, "load_fork_capability_invocations", None)
    if loader is None:
        raise RetryPlanRejected(
            "RETRY_INVOCATION_STORE_UNAVAILABLE",
            "Durable store cannot inspect source capability invocations.",
        )
    invocations = await loader(execution_id)
    if invocations:
        raise RetryPlanRejected(
            "RETRY_SIDE_EFFECT_RECONCILIATION_REQUIRED",
            "AE-R9-B fails closed when the source execution has capability "
            "invocations; checkpoint-directed safe reconstruction is owned "
            "by AE-R9-C.",
        )


async def _require_no_source_capability_side_effects_in_uow(
    uow,
    execution_id: str,
) -> None:
    repository = getattr(uow, "capability_invocations", None)
    if repository is None:
        raise RetryPlanRejected(
            "RETRY_INVOCATION_STORE_UNAVAILABLE",
            "Shared UoW has no capability invocation repository.",
        )
    invocations = await repository.list_records_for_execution(execution_id)
    if invocations:
        raise RetryPlanRejected(
            "RETRY_SIDE_EFFECT_RECONCILIATION_REQUIRED",
            "Source execution gained capability invocation authority after "
            "RETRY planning.",
        )


async def _load_optional_checkpoint(
    store,
    *,
    execution,
    task_id: str,
    branch_id: str,
    source_checkpoint_id: str | None,
):
    if source_checkpoint_id is None:
        return None
    if execution.current_checkpoint_id != source_checkpoint_id:
        raise RetryPlanRejected(
            "RETRY_CHECKPOINT_CONFLICT",
            "Requested RETRY checkpoint is not the source current checkpoint.",
        )
    checkpoint = await store.load_current_checkpoint(execution.id)
    if (
        checkpoint is None
        or checkpoint.checkpoint_id != source_checkpoint_id
        or checkpoint.execution_id != execution.id
        or checkpoint.session_id != execution.session_id
        or checkpoint.task_id != task_id
        or checkpoint.branch_id != branch_id
        or checkpoint.transcript_snapshot is None
        or int(checkpoint.execution_revision) > int(execution.revision)
    ):
        raise RetryPlanRejected(
            "RETRY_CHECKPOINT_CONFLICT",
            "Requested RETRY checkpoint has stale or foreign lineage.",
        )
    pending = await store.load_checkpoint_pending_invocations(
        source_checkpoint_id
    )
    if pending:
        raise RetryPlanRejected(
            "RETRY_CHECKPOINT_CONFLICT",
            "Requested RETRY checkpoint contains pending invocations.",
        )
    return checkpoint


class AgentRetryPlanningService:
    """AE-R9-B read-only planner for one same-Branch RETRY admission."""

    _TASK_SOURCE_STATES = frozenset({"RUNNING", "WAITING"})
    _RETRYABLE_SOURCE_STATES = frozenset({"FAILED", "TIMEOUT"})

    def __init__(self, durable_store) -> None:
        self._store = durable_store

    async def build_retry_plan(
        self,
        *,
        retry_request_id: str,
        task_id: str,
        branch_id: str,
        source_execution_id: str,
        target_user_id: str,
        source_checkpoint_id: str | None = None,
    ) -> RetryPlan:
        _require_identifier("retry_request_id", retry_request_id)
        _require_identifier("task_id", task_id)
        _require_identifier("branch_id", branch_id)
        _require_identifier("source_execution_id", source_execution_id)
        _require_identifier("target_user_id", target_user_id)
        if source_checkpoint_id is not None:
            _require_identifier("source_checkpoint_id", source_checkpoint_id)

        task = await self._store.load_task(task_id)
        if task is None:
            raise RetryPlanRejected(
                "RETRY_TASK_NOT_FOUND",
                f"Unknown AgentTask: {task_id}",
            )
        if str(task.created_by) != target_user_id:
            raise RetryPlanRejected(
                "RETRY_FOREIGN_PRINCIPAL",
                "AgentTask owner does not match RETRY principal.",
            )
        task_state = _value(task.status)
        if task_state not in self._TASK_SOURCE_STATES:
            raise RetryPlanRejected(
                "RETRY_TASK_TERMINAL",
                f"AgentTask is not RUNNING/WAITING: {task_state}.",
            )

        branch = await self._store.load_task_branch(branch_id)
        if branch is None:
            raise RetryPlanRejected(
                "RETRY_BRANCH_NOT_FOUND",
                f"Unknown TaskBranch: {branch_id}",
            )
        if str(branch.task_id) != task_id:
            raise RetryPlanRejected(
                "RETRY_BRANCH_TASK_CONFLICT",
                "TaskBranch belongs to another AgentTask.",
            )
        if _value(branch.resolution_state) != "OPEN":
            raise RetryPlanRejected(
                "RETRY_BRANCH_NOT_OPEN",
                "TaskBranch is not OPEN.",
            )
        if branch.current_execution_id != source_execution_id:
            raise RetryPlanRejected(
                "RETRY_SOURCE_NOT_CURRENT",
                "Source execution is not TaskBranch.current_execution_id.",
            )

        execution = await self._store.load_execution(source_execution_id)
        if execution is None:
            raise RetryPlanRejected(
                "RETRY_EXECUTION_NOT_FOUND",
                f"Unknown AgentExecution: {source_execution_id}",
            )
        if (
            execution.task_id != task_id
            or execution.branch_id != branch_id
            or execution.session_id != task.session_id
        ):
            raise RetryPlanRejected(
                "RETRY_EXECUTION_LINEAGE_CONFLICT",
                "Source execution task/branch/session lineage conflicts.",
            )
        if (
            execution.parent_execution_id is None
            and execution.agent_id != task.assigned_agent_id
        ):
            raise RetryPlanRejected(
                "RETRY_EXECUTION_LINEAGE_CONFLICT",
                "Root source execution differs from Task assigned agent.",
            )
        source_state = _value(execution.state)
        if source_state not in self._RETRYABLE_SOURCE_STATES:
            raise RetryPlanRejected(
                "RETRY_SOURCE_NOT_TERMINAL",
                "Initial AE-R9-B only retries FAILED/TIMEOUT executions.",
            )

        checkpoint = await _load_optional_checkpoint(
            self._store,
            execution=execution,
            task_id=task_id,
            branch_id=branch_id,
            source_checkpoint_id=source_checkpoint_id,
        )
        await _require_no_source_capability_side_effects(
            self._store,
            source_execution_id,
        )

        budget = await self._store.load_task_budget(task_id)
        if budget is None:
            raise RetryPlanRejected(
                "RETRY_TASK_BUDGET_REQUIRED",
                "Task has no durable TaskBudget.",
            )
        if _value(budget.state) != "OPEN":
            raise RetryPlanRejected(
                "RETRY_TASK_TERMINAL",
                "TaskBudget is CLOSED.",
            )
        if (
            int(budget.used_executions) >= int(budget.max_total_executions)
            or int(budget.active_executions)
            >= int(budget.max_active_executions)
        ):
            raise RetryPlanDeferred(
                "RETRY_BUDGET_EXCEEDED",
                "TaskBudget execution capacity is exhausted.",
            )
        delegated = execution.parent_execution_id is not None
        if delegated and (
            int(budget.active_parallel_agents)
            >= int(budget.max_parallel_agents)
        ):
            raise RetryPlanDeferred(
                "RETRY_BUDGET_EXCEEDED",
                "TaskBudget parallel-Agent capacity is exhausted.",
            )

        runtime_context_state = getattr(execution, "context_state", None)
        if not isinstance(runtime_context_state, Mapping):
            loader = getattr(
                self._store, "load_fork_admission_by_execution", None
            )
            receipt = (
                await loader(source_execution_id)
                if callable(loader)
                else None
            )
            runtime_context_state = _fork_seed_context_state(receipt)
        (
            request_fingerprint,
            source_context_fingerprint,
            fresh_active_budget_seconds,
        ) = _runtime_proof(execution, runtime_context_state)

        plan_values = {
            "task_id": task_id,
            "expected_task_revision": int(task.revision),
            "session_id": str(task.session_id),
            "branch_id": branch_id,
            "expected_branch_revision": int(branch.revision),
            "source_execution_id": source_execution_id,
            "expected_execution_revision": int(execution.revision),
            "source_execution_state": source_state,
            "source_agent_id": str(execution.agent_id),
            "source_checkpoint_id": (
                checkpoint.checkpoint_id if checkpoint is not None else None
            ),
            "expected_task_budget_revision": int(budget.revision),
            "budget_policy_fingerprint": str(budget.policy_fingerprint),
            "parent_execution_id": execution.parent_execution_id,
            "base_execution_id": execution.base_execution_id,
            "base_checkpoint_id": execution.base_checkpoint_id,
            "correlation_id": str(execution.correlation_id),
            "request_fingerprint": request_fingerprint,
            "source_context_fingerprint": source_context_fingerprint,
            "fresh_active_budget_seconds": fresh_active_budget_seconds,
            "target_user_id": target_user_id,
        }
        return RetryPlan(
            retry_request_id=retry_request_id,
            plan_fingerprint=retry_plan_fingerprint(plan_values),
            **plan_values,
        )


async def revalidate_retry_plan_in_uow(
    uow,
    plan: RetryPlan,
) -> RetryRevalidationSnapshot:
    """Re-prove every AE-R9-B fence under the consume transaction."""

    if retry_plan_fingerprint(plan) != plan.plan_fingerprint:
        raise RetryPlanRejected(
            "RETRY_PLAN_FINGERPRINT_INVALID",
            "RetryPlan semantic fingerprint no longer matches its payload.",
        )

    task = await uow.agents.get_task_for_update(plan.task_id)
    if task is None:
        raise RetryPlanRejected(
            "RETRY_TASK_NOT_FOUND",
            f"Unknown AgentTask: {plan.task_id}",
        )
    if (
        int(task.revision) != int(plan.expected_task_revision)
        or str(task.created_by) != plan.target_user_id
        or str(task.session_id) != plan.session_id
    ):
        raise RetryPlanRejected(
            "RETRY_TASK_CONFLICT",
            "AgentTask changed after RETRY planning.",
        )
    task_state = _value(task.status)
    if task_state not in AgentRetryPlanningService._TASK_SOURCE_STATES:
        raise RetryPlanRejected(
            "RETRY_TASK_TERMINAL",
            f"AgentTask is no longer RUNNING/WAITING: {task_state}.",
        )

    budget = await uow.agents.get_task_budget_for_update(plan.task_id)
    if budget is None:
        raise RetryPlanRejected(
            "RETRY_TASK_BUDGET_REQUIRED",
            "Task has no durable TaskBudget.",
        )
    if (
        _value(budget.state) != "OPEN"
        or int(budget.revision) != int(plan.expected_task_budget_revision)
        or str(budget.policy_fingerprint) != plan.budget_policy_fingerprint
    ):
        raise RetryPlanRejected(
            "RETRY_TASK_BUDGET_STALE",
            "TaskBudget changed after RETRY planning.",
        )
    if (
        int(budget.used_executions) >= int(budget.max_total_executions)
        or int(budget.active_executions)
        >= int(budget.max_active_executions)
    ):
        raise RetryPlanDeferred(
            "RETRY_BUDGET_EXCEEDED",
            "TaskBudget execution capacity is exhausted.",
        )

    branch = await uow.agents.get_task_branch_for_update(plan.branch_id)
    if (
        branch is None
        or branch.task_id != plan.task_id
        or int(branch.revision) != int(plan.expected_branch_revision)
        or _value(branch.resolution_state) != "OPEN"
        or branch.current_execution_id != plan.source_execution_id
    ):
        raise RetryPlanRejected(
            "RETRY_SOURCE_NOT_CURRENT",
            "TaskBranch changed after RETRY planning.",
        )

    execution = await uow.agents.get_execution_for_update(
        plan.source_execution_id
    )
    if (
        execution is None
        or execution.task_id != plan.task_id
        or execution.branch_id != plan.branch_id
        or execution.session_id != plan.session_id
        or execution.agent_id != plan.source_agent_id
        or execution.correlation_id != plan.correlation_id
        or int(execution.revision) != int(plan.expected_execution_revision)
        or _value(execution.state) != plan.source_execution_state
        or execution.parent_execution_id != plan.parent_execution_id
        or execution.base_execution_id != plan.base_execution_id
        or execution.base_checkpoint_id != plan.base_checkpoint_id
    ):
        raise RetryPlanRejected(
            "RETRY_EXECUTION_CONFLICT",
            "Source execution changed after RETRY planning.",
        )
    if (
        execution.parent_execution_id is None
        and str(task.assigned_agent_id) != plan.source_agent_id
    ):
        raise RetryPlanRejected(
            "RETRY_EXECUTION_LINEAGE_CONFLICT",
            "Root source execution differs from Task assigned agent.",
        )
    if _value(execution.state) not in AgentRetryPlanningService._RETRYABLE_SOURCE_STATES:
        raise RetryPlanRejected(
            "RETRY_SOURCE_NOT_TERMINAL",
            "Source execution is no longer retryable.",
        )

    runtime_context_state = getattr(execution, "context_state", None)
    if not isinstance(runtime_context_state, Mapping):
        receipt = await uow.agents.get_task_fork_admission_by_execution(
            execution.id
        )
        runtime_context_state = _fork_seed_context_state(receipt)
    (
        request_fingerprint,
        source_context_fingerprint,
        fresh_active_budget_seconds,
    ) = _runtime_proof(execution, runtime_context_state)
    if (
        request_fingerprint != plan.request_fingerprint
        or source_context_fingerprint != plan.source_context_fingerprint
        or fresh_active_budget_seconds != plan.fresh_active_budget_seconds
    ):
        raise RetryPlanRejected(
            "RETRY_RUNTIME_CONTEXT_CONFLICT",
            "Source retry runtime proof changed after planning.",
        )

    checkpoint = None
    if plan.source_checkpoint_id is not None:
        if execution.current_checkpoint_id != plan.source_checkpoint_id:
            raise RetryPlanRejected(
                "RETRY_CHECKPOINT_CONFLICT",
                "Source current checkpoint changed after RETRY planning.",
            )
        checkpoint = await uow.agents.get_execution_checkpoint(
            plan.source_checkpoint_id
        )
        if (
            checkpoint is None
            or checkpoint.execution_id != plan.source_execution_id
            or checkpoint.session_id != plan.session_id
            or checkpoint.task_id != plan.task_id
            or checkpoint.branch_id != plan.branch_id
            or checkpoint.transcript_snapshot is None
            or int(checkpoint.execution_revision)
            > int(plan.expected_execution_revision)
        ):
            raise RetryPlanRejected(
                "RETRY_CHECKPOINT_CONFLICT",
                "Source checkpoint changed after RETRY planning.",
            )
        pending = await uow.agents.list_checkpoint_pending_invocations(
            plan.source_checkpoint_id
        )
        if pending:
            raise RetryPlanRejected(
                "RETRY_CHECKPOINT_CONFLICT",
                "Source checkpoint gained pending invocation snapshots.",
            )

    await _require_no_source_capability_side_effects_in_uow(
        uow,
        plan.source_execution_id,
    )

    delegated = execution.parent_execution_id is not None
    if delegated and (
        int(budget.active_parallel_agents) >= int(budget.max_parallel_agents)
    ):
        raise RetryPlanDeferred(
            "RETRY_BUDGET_EXCEEDED",
            "TaskBudget parallel-Agent capacity is exhausted.",
        )

    return RetryRevalidationSnapshot(
        task=task,
        budget=budget,
        branch=branch,
        execution=execution,
        checkpoint=checkpoint,
        delegated=delegated,
        fresh_active_budget_seconds=fresh_active_budget_seconds,
        runtime_context_state=dict(runtime_context_state),
    )


__all__ = [
    "AgentRetryPlanningService",
    "RetryPlanDeferred",
    "RetryPlanError",
    "RetryPlanRejected",
    "RetryRevalidationSnapshot",
    "revalidate_retry_plan_in_uow",
]
