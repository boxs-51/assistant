from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts.fork import (
    ForkPlan,
    ForkSideEffectSnapshot,
    committed_result_fingerprint,
    fork_plan_fingerprint,
    fork_side_effect_fingerprint,
    fork_transcript_fingerprint,
)
from .contracts.inference import InferenceMessage
from .persistence import ExecutionConflictError
from .serialization import to_json_safe


class ForkPlanError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ForkPlanRejected(ForkPlanError):
    """The durable source cannot ever satisfy this exact FORK intent."""


class ForkPlanDeferred(ForkPlanError):
    """The source is valid but current capacity does not permit FORK."""


class AgentForkPlanningService:
    """R8-C read-only planner for one future R8-D sibling Branch consume."""

    _TASK_SOURCE_STATES = frozenset({"RUNNING", "WAITING"})
    _TERMINAL_INVOCATION_STATES = frozenset(
        {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}
    )
    _UNSAFE_REMOTE_OUTCOMES = frozenset(
        {"NOT_DISPATCHED", "IN_FLIGHT", "OUTCOME_UNKNOWN"}
    )

    def __init__(self, durable_store) -> None:
        self._store = durable_store

    async def build_fork_plan(
        self,
        *,
        fork_request_id: str,
        task_id: str,
        source_branch_id: str,
        source_execution_id: str,
        source_checkpoint_id: str,
        target_user_id: str,
        overlay_messages: Sequence[Mapping[str, Any]] = (),
    ) -> ForkPlan:
        self._require_identifier("fork_request_id", fork_request_id)
        self._require_identifier("task_id", task_id)
        self._require_identifier("source_branch_id", source_branch_id)
        self._require_identifier("source_execution_id", source_execution_id)
        self._require_identifier("source_checkpoint_id", source_checkpoint_id)
        self._require_identifier("target_user_id", target_user_id)

        task = await self._store.load_task(task_id)
        if task is None:
            raise ForkPlanRejected(
                "FORK_TASK_NOT_FOUND",
                f"Unknown AgentTask: {task_id}",
            )
        if str(task.created_by) != target_user_id:
            raise ForkPlanRejected(
                "FORK_FOREIGN_PRINCIPAL",
                "AgentTask owner does not match FORK principal.",
            )
        task_status = self._value(task.status)
        if task_status not in self._TASK_SOURCE_STATES:
            raise ForkPlanRejected(
                "FORK_TASK_STATE_INVALID",
                f"AgentTask {task_id} is not RUNNING/WAITING.",
            )

        branch = await self._store.load_task_branch(source_branch_id)
        if branch is None:
            raise ForkPlanRejected(
                "FORK_BRANCH_NOT_FOUND",
                f"Unknown TaskBranch: {source_branch_id}",
            )
        if str(branch.task_id) != task_id:
            raise ForkPlanRejected(
                "FORK_BRANCH_TASK_CONFLICT",
                "Source TaskBranch belongs to another AgentTask.",
            )
        if self._value(branch.resolution_state) != "OPEN":
            raise ForkPlanRejected(
                "FORK_BRANCH_NOT_OPEN",
                "Source TaskBranch is not OPEN.",
            )
        if branch.current_execution_id != source_execution_id:
            raise ForkPlanRejected(
                "FORK_BRANCH_CURRENT_EXECUTION_CONFLICT",
                "Source execution is not TaskBranch.current_execution_id.",
            )

        execution = await self._store.load_execution(source_execution_id)
        if execution is None:
            raise ForkPlanRejected(
                "FORK_EXECUTION_NOT_FOUND",
                f"Unknown AgentExecution: {source_execution_id}",
            )
        if (
            execution.task_id != task_id
            or execution.branch_id != source_branch_id
            or execution.session_id != task.session_id
            or execution.agent_id != task.assigned_agent_id
        ):
            raise ForkPlanRejected(
                "FORK_EXECUTION_LINEAGE_CONFLICT",
                "Source AgentExecution task/branch/session lineage conflicts.",
            )
        if self._value(execution.state) != "WAITING":
            raise ForkPlanRejected(
                "FORK_SOURCE_NOT_WAITING",
                "Initial R8-C only plans from stable WAITING executions.",
            )
        if execution.current_checkpoint_id != source_checkpoint_id:
            raise ForkPlanRejected(
                "FORK_STALE_CHECKPOINT",
                "Requested checkpoint is not AgentExecution.current_checkpoint_id.",
            )

        try:
            checkpoint = await self._store.load_current_checkpoint(
                source_execution_id
            )
        except ExecutionConflictError as exc:
            raise ForkPlanRejected(
                "FORK_STALE_CHECKPOINT",
                str(exc),
            ) from exc
        if checkpoint is None:
            raise ForkPlanRejected(
                "FORK_CHECKPOINT_NOT_FOUND",
                "Source execution has no normalized current checkpoint.",
            )
        if checkpoint.checkpoint_id != source_checkpoint_id:
            raise ForkPlanRejected(
                "FORK_STALE_CHECKPOINT",
                "Normalized current checkpoint differs from FORK request.",
            )
        if (
            checkpoint.execution_id != source_execution_id
            or checkpoint.execution_revision != execution.revision
            or checkpoint.session_id != execution.session_id
            or checkpoint.task_id != task_id
            or checkpoint.branch_id != source_branch_id
        ):
            raise ForkPlanRejected(
                "FORK_CHECKPOINT_LINEAGE_CONFLICT",
                "Normalized checkpoint lineage differs from source authority.",
            )
        if checkpoint.transcript_snapshot is None:
            raise ForkPlanRejected(
                "FORK_CHECKPOINT_TRANSCRIPT_UNAVAILABLE",
                "Initial R8-C requires inline checkpoint transcript_snapshot.",
            )

        pending = await self._store.load_checkpoint_pending_invocations(
            source_checkpoint_id
        )
        if pending:
            raise ForkPlanRejected(
                "FORK_PENDING_INVOCATIONS",
                "Source checkpoint was cut with pending invocation snapshots.",
            )

        side_effects = await self._load_safe_side_effects(
            source_execution_id
        )

        try:
            base_transcript = (
                await self._store.load_fork_safe_checkpoint_transcript(
                    source_execution_id,
                    source_checkpoint_id,
                )
            )
        except ExecutionConflictError as exc:
            code = str(exc).split(":", 1)[0]
            if not code.startswith("FORK_"):
                code = "FORK_TRANSCRIPT_UNSAFE"
            raise ForkPlanRejected(code, str(exc)) from exc

        budget = await self._store.load_task_budget(task_id)
        if budget is None:
            raise ForkPlanRejected(
                "FORK_TASK_BUDGET_REQUIRED",
                "Task has no durable TaskBudget.",
            )
        if self._value(budget.state) != "OPEN":
            raise ForkPlanRejected(
                "FORK_TASK_BUDGET_CLOSED",
                "TaskBudget is CLOSED.",
            )
        if int(budget.active_branches) >= int(budget.max_active_branches):
            raise ForkPlanDeferred(
                "FORK_BRANCH_CAPACITY_UNAVAILABLE",
                "TaskBudget max_active_branches is exhausted.",
            )
        if (
            int(budget.used_executions) >= int(budget.max_total_executions)
            or int(budget.active_executions)
            >= int(budget.max_active_executions)
        ):
            raise ForkPlanDeferred(
                "FORK_EXECUTION_CAPACITY_UNAVAILABLE",
                "TaskBudget execution capacity is exhausted.",
            )

        normalized_overlay = self._normalize_overlay(overlay_messages)
        transcript_fingerprint = fork_transcript_fingerprint(
            base_transcript
        )
        side_effect_fingerprint = fork_side_effect_fingerprint(
            side_effects
        )

        plan_values = {
            "task_id": task_id,
            "expected_task_revision": int(task.revision),
            "session_id": str(task.session_id),
            "source_branch_id": source_branch_id,
            "expected_branch_revision": int(branch.revision),
            "source_execution_id": source_execution_id,
            "expected_execution_revision": int(execution.revision),
            "source_agent_id": str(execution.agent_id),
            "correlation_id": str(execution.correlation_id),
            "source_checkpoint_id": source_checkpoint_id,
            "checkpoint_iteration": int(checkpoint.iteration),
            "expected_task_budget_revision": int(budget.revision),
            "budget_policy_fingerprint": str(
                budget.policy_fingerprint
            ),
            "base_transcript": tuple(base_transcript),
            "base_transcript_fingerprint": transcript_fingerprint,
            "side_effects": tuple(side_effects),
            "side_effect_fingerprint": side_effect_fingerprint,
            "overlay_messages": normalized_overlay,
            "target_user_id": target_user_id,
        }
        return ForkPlan(
            fork_request_id=fork_request_id,
            plan_fingerprint=fork_plan_fingerprint(plan_values),
            **plan_values,
        )

    async def _load_safe_side_effects(
        self,
        execution_id: str,
    ) -> tuple[ForkSideEffectSnapshot, ...]:
        invocations = await self._store.load_fork_capability_invocations(
            execution_id
        )
        snapshots: list[ForkSideEffectSnapshot] = []

        for invocation in invocations:
            state = self._value(invocation.state)
            if state not in self._TERMINAL_INVOCATION_STATES:
                raise ForkPlanRejected(
                    "FORK_SIDE_EFFECT_UNRESOLVED",
                    f"CapabilityInvocation {invocation.invocation_id} "
                    f"is still {state}.",
                )

            outcome = self._value(invocation.remote_outcome_state)
            if str(invocation.driver_kind or "") == "REMOTE_CLIENT":
                if outcome != "TERMINAL_COMMITTED":
                    raise ForkPlanRejected(
                        "FORK_REMOTE_OUTCOME_UNSAFE",
                        f"Remote invocation {invocation.invocation_id} "
                        "has no TERMINAL_COMMITTED authority.",
                    )
            elif outcome in self._UNSAFE_REMOTE_OUTCOMES:
                raise ForkPlanRejected(
                    "FORK_REMOTE_OUTCOME_UNSAFE",
                    f"Invocation {invocation.invocation_id} retains "
                    f"unsafe remote outcome {outcome}.",
                )

            tool_call_id = str(invocation.tool_call_id or "")
            if not tool_call_id:
                raise ForkPlanRejected(
                    "FORK_SIDE_EFFECT_PROJECTION_MISSING",
                    f"Invocation {invocation.invocation_id} has no "
                    "model-visible tool projection identity.",
                )

            result = await self._store.load_fork_committed_tool_result(
                execution_id,
                tool_call_id,
            )
            if result is None:
                raise ForkPlanRejected(
                    "FORK_COMMITTED_RESULT_MISSING",
                    f"Invocation {invocation.invocation_id} has no "
                    "already-COMMITTED AgentToolResult.",
                )
            expected_identity = {
                "execution_id": execution_id,
                "invocation_id": invocation.invocation_id,
                "tool_call_id": tool_call_id,
                "capability_id": invocation.capability_id,
            }
            for field, expected in expected_identity.items():
                if getattr(result, field, None) != expected:
                    raise ForkPlanRejected(
                        "FORK_COMMITTED_RESULT_CONFLICT",
                        f"AgentToolResult {field} differs from "
                        "CapabilityInvocation authority.",
                    )

            if str(invocation.driver_kind or "") == "REMOTE_CLIENT":
                self._validate_remote_projection(invocation, result)

            snapshots.append(
                ForkSideEffectSnapshot(
                    invocation_id=str(invocation.invocation_id),
                    invocation_revision=int(invocation.revision),
                    capability_id=str(invocation.capability_id),
                    capability_version=(
                        str(invocation.capability_version)
                        if invocation.capability_version is not None
                        else None
                    ),
                    request_fingerprint=(
                        str(invocation.request_fingerprint)
                        if invocation.request_fingerprint is not None
                        else None
                    ),
                    idempotency=self._value(invocation.idempotency),
                    state=state,
                    remote_outcome_state=outcome,
                    tool_call_id=tool_call_id,
                    committed_result_fingerprint=(
                        committed_result_fingerprint(result)
                    ),
                )
            )

        snapshots.sort(key=lambda item: item.invocation_id)
        return tuple(snapshots)

    @staticmethod
    def _validate_remote_projection(invocation, result) -> None:
        succeeded = AgentForkPlanningService._value(
            invocation.state
        ) == "COMPLETED"
        error = dict(invocation.error or {})
        expected = {
            "success": succeeded,
            "output": invocation.output,
            "error_code": (
                None
                if succeeded
                else error.get("error_code") or error.get("code")
            ),
            "error_message": (
                None
                if succeeded
                else error.get("error_message") or error.get("message")
            ),
            "retryable": (
                False
                if succeeded
                else bool(error.get("retryable", False))
            ),
        }
        for field, value in expected.items():
            if getattr(result, field, None) != value:
                raise ForkPlanRejected(
                    "FORK_COMMITTED_RESULT_CONFLICT",
                    f"Committed remote projection {field} differs "
                    "from terminal invocation authority.",
                )

    @staticmethod
    def _normalize_overlay(
        overlay_messages: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(overlay_messages):
            if not isinstance(raw, Mapping):
                raise ForkPlanRejected(
                    "FORK_OVERLAY_INVALID",
                    f"overlay_messages[{index}] is not a mapping.",
                )
            try:
                safe = to_json_safe(
                    dict(raw),
                    path=f"fork_plan.overlay_messages[{index}]",
                )
                message = InferenceMessage.model_validate(safe)
            except Exception as exc:
                raise ForkPlanRejected(
                    "FORK_OVERLAY_INVALID",
                    f"overlay_messages[{index}] is not a valid "
                    "InferenceMessage.",
                ) from exc
            normalized.append(message.model_dump(mode="json"))
        return tuple(normalized)

    @staticmethod
    def _require_identifier(name: str, value: str) -> None:
        if not str(value or "").strip():
            raise ForkPlanRejected(
                "FORK_REQUEST_INVALID",
                f"{name} must be non-empty.",
            )

    @staticmethod
    def _value(value: Any) -> str | None:
        if value is None:
            return None
        return str(getattr(value, "value", value))


__all__ = [
    "AgentForkPlanningService",
    "ForkPlanDeferred",
    "ForkPlanError",
    "ForkPlanRejected",
]
