from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError, OperationalError

from ...domain.schemas.task_budget import (
    TaskBudget,
    TaskBudgetLimits,
    TaskBudgetPolicy,
    TaskBudgetReservationKind,
    TaskBudgetState,
    normalize_task_budget_cost,
    task_budget_policy_fingerprint,
)
from .serialization import to_json_safe


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


_EXECUTION_JSON_FIELDS = frozenset({
    "request",
    "result",
    "context_state",
    "transcript",
    "inference_request",
    "inference_response",
})


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


class TaskBudgetService:
    """Durable TaskBudget policy and transaction coordinator.

    R5-B exposes reservation primitives only.  R5-C is responsible for wiring
    them into AgentRuntime state transitions.
    """

    def __init__(
        self,
        uow_factory,
        *,
        max_conflict_retries: int = 8,
    ) -> None:
        self._uow_factory = uow_factory
        self._max_conflict_retries = max(1, int(max_conflict_retries))

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

        async def side_effect(uow):
            existing = await uow.agents.get_execution(execution_id)
            if existing is not None:
                raise TaskBudgetConflictError(
                    "Execution exists without matching TaskBudget reservation."
                )
            await uow.agents.save_execution(normalized_execution_values)

        async def verify_idempotent(uow):
            existing = await uow.agents.get_execution(execution_id)
            if existing is None or existing.task_id != task_id:
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
    "DelegationDepthExceededError",
    "TaskBudgetClosedError",
    "TaskBudgetConflictError",
    "TaskBudgetError",
    "TaskBudgetExceededError",
    "TaskBudgetLegacyUninitializedError",
    "TaskBudgetRequiredError",
    "TaskBudgetService",
]
