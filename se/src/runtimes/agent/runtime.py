from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ...domain.schemas.agent_execution import (
    AgentExecutionState,
    AgentExecutionWaitReason,
    normalize_execution_waiting,
)

from .contracts import (
    AgentContextRequest,
    AgentExecutionContext,
    AgentExecutionResult,
    AgentIteration,
    AgentLoopState,
    InferenceMessage,
    InferencePort,
    InferenceRequest,
    ToolExecutionPort,
    ToolExecutionRequest,
    ToolExecutionResult,
    transition,
)
from .contracts.context_builder import AgentContextHistoryMode
from .contracts.policy import AgentExecutionPolicy, PolicyDecision
from .contracts.events import (
    AgentEventEnvelope,
    AgentEventName,
    AgentEventPublisher,
    CorrelationContext,
)
from .contracts.resume import (
    ResumeClaimConsumeResult,
    ResumeInvocationAction,
    ResumeInvocationActionKind,
    ResumePlan,
    resume_plan_fingerprint,
)
from .persistence import ExecutionConflictError
from .resume_claim import ResumeActivationError
from .state_machine import AgentExecutionStateMachine
from .wait_policy import (
    ConfiguredExecutionWaitPolicy,
    ExecutionWaitPolicy,
)


class ExecutionWaitExpiredError(ExecutionConflictError):
    """A durable WAITING execution reached its wall-clock expiry."""


class ExecutionResumeBudgetError(ExecutionConflictError):
    """A durable WAITING execution has no resumable active-time budget."""


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class AgentRuntime:
    """Single-agent execution authority.

    The runtime owns only the loop lifecycle. Context, inference and tool
    implementations remain behind their respective ports.
    """

    def __init__(
        self,
        *,
        context_builder,
        inference: InferencePort,
        tool_execution: ToolExecutionPort,
        execution_policy: AgentExecutionPolicy,
        durable_store=None,
        event_publisher: AgentEventPublisher | None = None,
        wait_policy: ExecutionWaitPolicy | None = None,
        task_budget_service=None,
    ) -> None:
        self._context_builder = context_builder
        self._inference = inference
        self._tool_execution = tool_execution
        self._execution_policy = execution_policy
        self._durable_store = durable_store
        self._event_publisher = event_publisher
        self._task_budget_service = task_budget_service
        self._wait_policy = (
            wait_policy or ConfiguredExecutionWaitPolicy()
        )

    def _uses_task_budget(
        self,
        context: AgentExecutionContext,
    ) -> bool:
        return (
            context.task_id is not None
            and self._task_budget_service is not None
        )

    @staticmethod
    def _task_tool_call_payload(
        request: ToolExecutionRequest,
    ) -> dict[str, Any]:
        return {
            "execution_id": request.execution_id,
            "tool_call_id": request.tool_call_id,
            "capability_id": request.capability_id,
            "arguments": dict(request.arguments),
        }

    async def _reserve_task_tool_calls(
        self,
        context: AgentExecutionContext,
        requests: Sequence[ToolExecutionRequest],
    ) -> None:
        if not requests or not self._uses_task_budget(context):
            return
        assert context.task_id is not None
        await self._task_budget_service.reserve_tool_call_batch(
            context.task_id,
            [
                self._task_tool_call_payload(request)
                for request in requests
            ],
        )

    async def _transition_running_durable(
        self,
        context: AgentExecutionContext,
        revision: int,
        values: dict[str, Any],
        *,
        checkpoint_values: dict[str, Any] | None = None,
        pending_invocations: Sequence[dict[str, Any]] = (),
    ) -> int:
        if self._uses_task_budget(context):
            assert context.task_id is not None
            target_revision = await (
                self._task_budget_service.finish_task_scoped_execution(
                    context.task_id,
                    execution_id=context.execution_id,
                    source_revision=revision,
                    transition_values=values,
                    delegated=context.parent_execution_id is not None,
                    checkpoint_values=checkpoint_values,
                    pending_invocations=pending_invocations,
                )
            )
            reconciler = getattr(
                self._task_budget_service,
                "reconcile_multibranch_task_activity",
                None,
            )
            if callable(reconciler):
                await reconciler(context.task_id)
            return target_revision
        if checkpoint_values is not None:
            writer = getattr(
                self._durable_store,
                "commit_waiting_checkpoint",
                None,
            )
            if not callable(writer):
                raise ExecutionConflictError(
                    "NORMALIZED_WAITING_AUTHORITY_UNAVAILABLE: durable "
                    "WAITING requires commit_waiting_checkpoint()."
                )
            await writer(
                context.execution_id,
                revision,
                values,
                checkpoint_values=checkpoint_values,
                pending_invocations=list(pending_invocations),
            )
            return revision + 1
        await self._durable_store.compare_and_set_execution(
            context.execution_id,
            revision,
            values,
        )
        return revision + 1

    async def _publish(
        self,
        event_name: str,
        context: AgentExecutionContext,
        *,
        iteration: int | None = None,
        request_id: str | None = None,
        tool_call_id: str | None = None,
        invocation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._event_publisher is None:
            return
        event = AgentEventEnvelope(
            event_id=f"{context.execution_id}:{event_name}:{uuid.uuid4().hex}",
            event_name=event_name,
            correlation=CorrelationContext(
                correlation_id=context.correlation_id,
                session_id=context.session_id,
                execution_id=context.execution_id,
                task_id=context.task_id,
                branch_id=context.branch_id,
                request_id=request_id or context.request_id,
                parent_execution_id=context.parent_execution_id,
                iteration_id=(
                    f"{context.execution_id}:iteration:{iteration}"
                    if iteration is not None
                    else None
                ),
                tool_call_id=tool_call_id,
                invocation_id=invocation_id,
                causation_id=context.causation_id,
                trace_id=context.trace_id,
            ),
            payload=payload or {},
        )
        try:
            await self._event_publisher.publish(event)
        except Exception:
            # Observability must not change the execution result.
            return

    async def _persist_iteration(self, record: AgentIteration) -> None:
        if self._durable_store is None:
            return
        values = {
            "id": f"{record.execution_id}:iteration:{record.iteration}",
            "execution_id": record.execution_id,
            "iteration": record.iteration,
            "state": record.state.value,
            "inference_request_id": record.inference_request_id,
            "tool_call_ids": record.tool_call_ids,
            "error_code": record.error_code,
            "completed_at": record.completed_at,
        }
        existing = await self._durable_store.load_iteration(
            record.execution_id,
            iteration_number=record.iteration,
        )
        if existing is None:
            await self._durable_store.save_iteration(values)
        else:
            await self._durable_store.update_iteration(existing.id, values)

    async def _persist_execution_checkpoint(
        self,
        context: AgentExecutionContext,
        transcript: Sequence[InferenceMessage],
        *,
        inference_request: InferenceRequest | None = None,
        inference_response=None,
    ) -> None:
        if self._durable_store is None:
            return
        context_state = {
            "request_id": context.request_id,
            "parent_execution_id": context.parent_execution_id,
            "workflow_id": context.workflow_id,
            "metadata": context.metadata,
            "causation_id": context.causation_id,
            "trace_id": context.trace_id,
            "connection_id": context.connection_id,
            "limits": context.limits.model_dump(mode="json"),
        }
        values = {
            "context_state": context_state,
            "transcript": [item.model_dump(mode="json") for item in transcript],
        }
        if inference_request is not None:
            values["inference_request"] = {
                "request_id": inference_request.request_id,
                "execution_id": inference_request.execution_id,
                "iteration": inference_request.iteration,
                "messages": [
                    item.model_dump(mode="json") for item in inference_request.messages
                ],
                "tools": [item.model_dump(mode="json") for item in inference_request.tools],
                "model": inference_request.model,
                "metadata": inference_request.metadata,
            }
        if inference_response is not None:
            values["inference_response"] = {
                "request_id": inference_response.request_id,
                "execution_id": inference_response.execution_id,
                "iteration": inference_response.iteration,
                "message": inference_response.message.model_dump(mode="json"),
                "finish_reason": inference_response.finish_reason,
                "usage": inference_response.usage.model_dump(mode="json"),
                "provider": inference_response.provider,
                "model": inference_response.model,
                "metadata": inference_response.metadata,
            }
        await self._durable_store.update_checkpoint(context.execution_id, values)

    async def _load_committed_tool_result(
        self,
        request: ToolExecutionRequest,
    ) -> ToolExecutionResult | None:
        if self._durable_store is None:
            return None
        committed_loader = getattr(
            self._durable_store,
            "load_committed_tool_result",
            None,
        )
        if callable(committed_loader):
            record = await committed_loader(
                request.execution_id,
                request.tool_call_id,
            )
        else:
            record = await self._durable_store.load_tool_result(
                request.execution_id,
                request.tool_call_id,
            )
            if (
                record is not None
                and getattr(record, "commit_state", "PROVISIONAL")
                != "COMMITTED"
            ):
                record = None
        if record is None:
            return None
        expected_identity = {
            "execution_id": request.execution_id,
            "invocation_id": request.invocation_id,
            "tool_call_id": request.tool_call_id,
            "capability_id": request.capability_id,
        }
        for key, expected in expected_identity.items():
            actual = getattr(record, key, None)
            if actual != expected:
                raise ExecutionConflictError(
                    f"Committed tool-result {key} does not match resume request: "
                    f"{actual!r} != {expected!r}."
                )
        return ToolExecutionResult(
            execution_id=record.execution_id,
            iteration=request.iteration,
            invocation_id=record.invocation_id,
            tool_call_id=record.tool_call_id,
            capability_id=record.capability_id,
            success=record.success,
            output=record.output,
            error_code=record.error_code,
            error_message=record.error_message,
            retryable=record.retryable,
            metadata=record.extra_metadata or {},
        )

    async def _execute_resumed_tool_calls(
        self,
        context: AgentExecutionContext,
    ) -> tuple[ToolExecutionResult, ...]:
        requests = [
            ToolExecutionRequest.model_validate(item)
            for item in context.resume_pending_tool_calls
        ]
        if not requests:
            return ()
        normalized_requests: list[ToolExecutionRequest] = []
        for request in requests:
            if request.connection_id is None and context.connection_id is not None:
                request = request.model_copy(
                    update={"connection_id": context.connection_id}
                )
            normalized_requests.append(request)
        requests = normalized_requests
        committed: list[ToolExecutionResult] = []
        pending: list[ToolExecutionRequest] = []
        for request in requests:
            result = await self._load_committed_tool_result(request)
            if result is None:
                pending.append(request)
            else:
                committed.append(result)
        executed = []
        if pending:
            await self._reserve_task_tool_calls(context, pending)
            raw = await self._await_contextual(
                self._tool_execution.execute_many(
                    context,
                    pending,
                    max_parallel=context.limits.max_parallel_tools,
                ),
                context=context,
                timeout_seconds=context.remaining_iteration_seconds,
            )
            executed = list(_order_tool_results(pending, raw))
            iteration_id = f"{context.execution_id}:iteration:{context.iteration}"
            committed_executed: list[ToolExecutionResult] = []
            for result in executed:
                await self._persist_tool_result(result, iteration_id)
                if self._durable_store is None:
                    committed_executed.append(result)
                    continue
                request = next(
                    item
                    for item in pending
                    if item.tool_call_id == result.tool_call_id
                )
                committed_result = await self._load_committed_tool_result(request)
                if committed_result is None:
                    raise ExecutionConflictError(
                        "Resumed tool outcome is not COMMITTED and cannot "
                        "enter model context."
                    )
                committed_executed.append(committed_result)
            executed = committed_executed
        by_id = {item.tool_call_id: item for item in [*committed, *executed]}
        return tuple(by_id[item.tool_call_id] for item in requests)

    @staticmethod
    def _resume_action_request(
        context: AgentExecutionContext,
        action: ResumeInvocationAction,
    ) -> ToolExecutionRequest:
        return ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=context.iteration,
            invocation_id=action.invocation_id,
            tool_call_id=action.tool_call_id,
            capability_id=action.capability_id,
            connection_id=context.connection_id,
            arguments={},
            metadata={"r7_resume_action": action.action.value},
        )

    async def _activate_claimed_resume_context(
        self,
        context: AgentExecutionContext,
        plan: ResumePlan,
        consumed: ResumeClaimConsumeResult,
    ) -> None:
        """Verify F3 authority and start the active monotonic budget."""

        if self._durable_store is None:
            raise ResumeActivationError(
                "RESUME_AUTHORITY_UNAVAILABLE",
                "Claimed resume requires durable AgentExecution storage.",
            )
        if resume_plan_fingerprint(plan) != plan.plan_fingerprint:
            raise ResumeActivationError(
                "STALE_RESUME_PLAN",
                "ResumePlan fingerprint changed after claim consumption.",
            )
        if (
            consumed.execution_id != plan.execution_id
            or consumed.checkpoint_id != plan.checkpoint_id
            or consumed.source_execution_revision
            != plan.expected_execution_revision
            or consumed.consumed_execution_revision
            != plan.expected_execution_revision + 1
            or consumed.bound_client_id != plan.target_client_id
            or consumed.bound_connection_id != plan.target_connection_id
            or float(consumed.remaining_active_budget_seconds)
            != float(plan.remaining_active_budget_seconds)
        ):
            raise ResumeActivationError(
                "STALE_RESUME_CLAIM",
                "Consumed ResumeClaim authority does not match ResumePlan.",
            )
        if (
            context.execution_id != plan.execution_id
            or context.session_id != plan.session_id
            or context.agent_id != plan.agent_id
            or context.task_id != plan.task_id
            or context.branch_id != plan.branch_id
            or context.parent_execution_id != plan.parent_execution_id
            or context.retry_of_execution_id != plan.retry_of_execution_id
            or context.base_execution_id != plan.base_execution_id
            or context.base_checkpoint_id != plan.base_checkpoint_id
            or context.correlation_id != plan.correlation_id
            or context.identity.user_id != plan.target_user_id
            or context.resume_revision != plan.expected_execution_revision
        ):
            raise ResumeActivationError(
                "STALE_RESUME_CONTEXT",
                "Prepared resume context no longer matches ResumePlan.",
            )

        execution = await self._durable_store.load_execution(
            plan.execution_id
        )
        if execution is None:
            raise ResumeActivationError(
                "RESUME_AUTHORITY_UNAVAILABLE",
                "Consumed AgentExecution is missing.",
            )
        if (
            str(execution.state) != AgentExecutionState.RUNNING.value
            or execution.revision != consumed.consumed_execution_revision
            or execution.current_checkpoint_id != plan.checkpoint_id
            or execution.bound_client_id != plan.target_client_id
            or execution.bound_connection_id != plan.target_connection_id
            or execution.session_id != plan.session_id
            or execution.agent_id != plan.agent_id
            or execution.task_id != plan.task_id
            or execution.branch_id != plan.branch_id
            or execution.parent_execution_id != plan.parent_execution_id
            or execution.retry_of_execution_id != plan.retry_of_execution_id
            or execution.base_execution_id != plan.base_execution_id
            or execution.base_checkpoint_id != plan.base_checkpoint_id
            or execution.correlation_id != plan.correlation_id
        ):
            raise ResumeActivationError(
                "STALE_RESUME_AUTHORITY",
                "Durable RUNNING authority differs from consumed ResumeClaim.",
            )
        durable_remaining = getattr(
            execution,
            "remaining_active_budget_seconds",
            None,
        )
        if (
            durable_remaining is None
            or float(durable_remaining)
            != float(consumed.remaining_active_budget_seconds)
        ):
            raise ResumeActivationError(
                "STALE_RESUME_AUTHORITY",
                "Durable active budget differs from consumed ResumeClaim.",
            )

        context.connection_id = plan.target_connection_id
        context.metadata["client_id"] = plan.target_client_id
        context.wait_expires_at = None
        context.resume_revision = consumed.consumed_execution_revision
        context.remaining_active_budget_seconds = (
            consumed.remaining_active_budget_seconds
        )
        context.restore_active_budget(
            consumed.remaining_active_budget_seconds
        )

    async def _load_committed_tool_result_by_id(
        self,
        context: AgentExecutionContext,
        tool_call_id: str,
    ) -> ToolExecutionResult:
        """Load one canonical active-batch slot without a pending R7 action."""

        if self._durable_store is None:
            raise ResumeActivationError(
                "RESUME_AUTHORITY_UNAVAILABLE",
                "R7-F4 requires durable tool-result commitment.",
            )
        loader = getattr(
            self._durable_store,
            "load_committed_tool_result",
            None,
        )
        if not callable(loader):
            raise ResumeActivationError(
                "RESUME_AUTHORITY_UNAVAILABLE",
                "Durable store cannot load committed tool results.",
            )
        record = await loader(context.execution_id, tool_call_id)
        if (
            record is None
            or getattr(record, "commit_state", "PROVISIONAL") != "COMMITTED"
            or record.execution_id != context.execution_id
            or record.tool_call_id != tool_call_id
        ):
            raise ResumeActivationError(
                "STALE_RECONCILIATION_SNAPSHOT",
                "Canonical active-batch slot is no longer COMMITTED.",
            )
        return ToolExecutionResult(
            execution_id=record.execution_id,
            iteration=context.iteration,
            invocation_id=record.invocation_id,
            tool_call_id=record.tool_call_id,
            capability_id=record.capability_id,
            success=record.success,
            output=record.output,
            error_code=record.error_code,
            error_message=record.error_message,
            retryable=record.retryable,
            metadata=dict(record.extra_metadata or {}),
        )

    async def _execute_resume_plan_actions(
        self,
        context: AgentExecutionContext,
        plan: ResumePlan,
    ) -> tuple[ToolExecutionResult, ...]:
        """Resolve every R7-D action without creating a new logical invocation."""

        if self._durable_store is None:
            raise ResumeActivationError(
                "RESUME_AUTHORITY_UNAVAILABLE",
                "R7-F4 requires durable tool-result commitment.",
            )

        actions = tuple(
            sorted(plan.invocation_actions, key=lambda item: item.ordinal)
        )
        ordered_ids = tuple(plan.ordered_tool_call_ids)
        if (
            len(set(ordered_ids)) != len(ordered_ids)
            or len({item.ordinal for item in actions}) != len(actions)
            or len({item.invocation_id for item in actions}) != len(actions)
            or len({item.tool_call_id for item in actions}) != len(actions)
            or any(
                item.ordinal < 0
                or item.ordinal >= len(ordered_ids)
                or ordered_ids[item.ordinal] != item.tool_call_id
                for item in actions
            )
        ):
            raise ResumeActivationError(
                "STALE_RESUME_PLAN",
                "Resume action ordering differs from canonical tool-call order.",
            )

        by_tool_call: dict[str, ToolExecutionResult] = {}
        action_ids = {item.tool_call_id for item in actions}
        for tool_call_id in ordered_ids:
            if tool_call_id in action_ids:
                continue
            by_tool_call[tool_call_id] = (
                await self._load_committed_tool_result_by_id(
                    context,
                    tool_call_id,
                )
            )
        continuation_actions: list[ResumeInvocationAction] = []

        for action in actions:
            if action.action is ResumeInvocationActionKind.REUSE_COMMITTED:
                request = self._resume_action_request(context, action)
                committed = await self._load_committed_tool_result(request)
                if committed is None:
                    raise ResumeActivationError(
                        "STALE_RECONCILIATION_SNAPSHOT",
                        "REUSE_COMMITTED result is no longer model-consumable.",
                    )
                by_tool_call[action.tool_call_id] = committed
            else:
                continuation_actions.append(action)

        if continuation_actions:
            continuation_runner = getattr(
                self._tool_execution,
                "continue_invocations",
                None,
            )
            if not callable(continuation_runner):
                raise ResumeActivationError(
                    "R7_CONTINUATION_UNAVAILABLE",
                    "Tool execution boundary does not expose R7-E continuation.",
                )

            raw_results = await self._await_contextual(
                continuation_runner(
                    context,
                    continuation_actions,
                    max_parallel=context.limits.max_parallel_tools,
                ),
                context=context,
                timeout_seconds=context.remaining_iteration_seconds,
            )
            raw_by_id = {
                item.tool_call_id: item for item in raw_results
            }
            if len(raw_by_id) != len(continuation_actions):
                raise ResumeActivationError(
                    "R7_CONTINUATION_RESULT_CONFLICT",
                    "Continuation batch returned duplicate or missing results.",
                )

            iteration_id = (
                f"{context.execution_id}:iteration:{context.iteration}"
            )
            for action in continuation_actions:
                result = raw_by_id.get(action.tool_call_id)
                if (
                    result is None
                    or result.execution_id != context.execution_id
                    or result.invocation_id != action.invocation_id
                    or result.capability_id != action.capability_id
                ):
                    raise ResumeActivationError(
                        "R7_CONTINUATION_RESULT_CONFLICT",
                        "Continuation result identity differs from ResumePlan.",
                    )

                request = self._resume_action_request(context, action)
                committed = await self._load_committed_tool_result(request)
                if committed is None:
                    # Persist transport output only when no durable committed
                    # projection has already won a post-claim race. This keeps
                    # stale continuation failures from conflicting with an
                    # authoritative terminal result committed by another actor.
                    await self._persist_tool_result(result, iteration_id)
                    committed = await self._load_committed_tool_result(request)
                if committed is None:
                    # A connection loss / stale R7-E fence can leave the
                    # invocation non-terminal. Never turn that provisional
                    # transport outcome into model context. R7-G owns recovery.
                    raise ResumeActivationError(
                        "RESUME_ACTION_NOT_COMMITTED",
                        "Continued invocation has no TERMINAL_COMMITTED projection.",
                        retryable=True,
                    )
                by_tool_call[action.tool_call_id] = committed

        if set(by_tool_call) != set(ordered_ids):
            raise ResumeActivationError(
                "R7_CONTINUATION_RESULT_CONFLICT",
                "Resume action batch did not produce complete committed coverage.",
            )
        return tuple(
            by_tool_call[tool_call_id]
            for tool_call_id in ordered_ids
        )

    async def prepare_claimed_resume_activation(
        self,
        context: AgentExecutionContext,
        *,
        plan: ResumePlan,
        consumed: ResumeClaimConsumeResult,
    ) -> tuple[ToolExecutionResult, ...]:
        """Finish the R7-F4 activation stage under supervisor ownership.

        R7-G uses this as the pre-ACK readiness barrier. The caller MUST run
        it inside the task returned by AgentExecutionSupervisor.start_reserved.
        No accepted ACK is safe until this method has returned successfully.
        """

        await self._activate_claimed_resume_context(
            context,
            plan,
            consumed,
        )

        context.begin_iteration_budget()
        try:
            resumed_results = await self._execute_resume_plan_actions(
                context,
                plan,
            )
            transcript = [
                InferenceMessage.model_validate(item)
                for item in context.resume_transcript
            ]
            transcript.extend(_tool_results_to_messages(resumed_results))
            context.resume_transcript = [
                item.model_dump(mode="json") for item in transcript
            ]
            context.resume_pending_tool_calls = []
            await self._persist_execution_checkpoint(
                context,
                transcript,
            )
            return resumed_results
        finally:
            context.clear_iteration_budget()

    async def recover_claimed_resume(
        self,
        context: AgentExecutionContext,
        *,
        plan: ResumePlan,
        consumed: ResumeClaimConsumeResult,
        error_message: str,
    ) -> tuple[str, int]:
        """Return a post-claim handoff failure to a fresh RECOVERY safe point.

        The consumed claim remains CONSUMED. This transition owns the same
        RUNNING revision acquired by F3 and, for task-scoped executions,
        releases active capacity in the same UoW as the recovery checkpoint.
        """

        if self._durable_store is None:
            raise ResumeActivationError(
                "RESUME_AUTHORITY_UNAVAILABLE",
                "Recovery requires durable AgentExecution storage.",
            )

        execution = await self._durable_store.load_execution(plan.execution_id)
        if execution is None:
            raise ResumeActivationError(
                "RESUME_AUTHORITY_UNAVAILABLE",
                "Consumed AgentExecution is missing during recovery.",
            )
        if (
            str(execution.state) != AgentExecutionState.RUNNING.value
            or execution.revision != consumed.consumed_execution_revision
            or execution.current_checkpoint_id != plan.checkpoint_id
            or execution.bound_client_id != consumed.bound_client_id
            or execution.bound_connection_id != consumed.bound_connection_id
        ):
            raise ResumeActivationError(
                "STALE_RESUME_AUTHORITY",
                "Recovery no longer owns the consumed RUNNING revision.",
            )

        remaining = context.freeze_active_budget()
        if remaining is None:
            remaining = consumed.remaining_active_budget_seconds
            context.remaining_active_budget_seconds = remaining
        remaining = max(0.0, float(remaining))

        target_revision = consumed.consumed_execution_revision + 1
        checkpoint_id = (
            f"{context.execution_id}:checkpoint:{target_revision}"
        )
        recovery_reason = AgentExecutionWaitReason.RECOVERY
        ttl_seconds = self._wait_policy.wait_ttl_seconds(
            reason=recovery_reason,
            context=context,
        )
        context.wait_expires_at = (
            None
            if ttl_seconds is None
            else context.clock.now_utc()
            + timedelta(seconds=ttl_seconds)
        )

        transcript_snapshot = (
            list(context.resume_transcript)
            if context.resume_transcript
            else [
                item.model_dump(mode="json")
                for item in plan.transcript_snapshot
            ]
        )
        pending_invocations = tuple(
            {
                "ordinal": action.ordinal,
                "invocation_id": action.invocation_id,
                "tool_call_id": action.tool_call_id,
                "capability_id": action.capability_id,
            }
            for action in plan.invocation_actions
            if action.action is not ResumeInvocationActionKind.REUSE_COMMITTED
        )
        checkpoint_values = {
            "checkpoint_id": checkpoint_id,
            "execution_id": context.execution_id,
            "execution_revision": target_revision,
            "session_id": context.session_id,
            "task_id": context.task_id,
            "branch_id": context.branch_id,
            "iteration": context.iteration,
            "wait_reason": recovery_reason.value,
            "remaining_active_budget_seconds": remaining,
            "wait_expires_at": context.wait_expires_at,
            "origin_client_id": plan.target_client_id,
            "origin_connection_id": plan.target_connection_id,
            "transcript_snapshot": transcript_snapshot,
            "metadata_json": {
                "request_id": context.request_id,
                "correlation_id": context.correlation_id,
                "trace_id": context.trace_id,
                "resume_request_id": consumed.resume_request_id,
                "claim_id": consumed.claim_id,
                "recovery_error": error_message,
            },
        }
        await self._transition_running_durable(
            context,
            consumed.consumed_execution_revision,
            {
                "state": AgentExecutionState.WAITING.value,
                "wait_reason": recovery_reason.value,
                "wait_expires_at": context.wait_expires_at,
                "remaining_active_budget_seconds": remaining,
                "bound_client_id": plan.target_client_id,
                "bound_connection_id": None,
                "error": error_message,
                "completed_at": None,
            },
            checkpoint_values=checkpoint_values,
            pending_invocations=pending_invocations,
        )
        context.resume_revision = target_revision
        context.connection_id = None
        context.remaining_active_budget_seconds = remaining
        return checkpoint_id, target_revision

    async def fail_claimed_resume(
        self,
        context: AgentExecutionContext,
        *,
        plan: ResumePlan,
        consumed: ResumeClaimConsumeResult,
        error_message: str,
    ) -> bool:
        """Fail closed if RECOVERY checkpointing itself cannot be established."""

        if self._durable_store is None:
            return False
        execution = await self._durable_store.load_execution(plan.execution_id)
        if (
            execution is None
            or str(execution.state) != AgentExecutionState.RUNNING.value
            or execution.revision != consumed.consumed_execution_revision
        ):
            return False

        context.freeze_active_budget()
        await self._transition_running_durable(
            context,
            consumed.consumed_execution_revision,
            {
                "state": AgentExecutionState.FAILED.value,
                "wait_reason": None,
                "wait_expires_at": None,
                "bound_connection_id": None,
                "error": error_message,
                "completed_at": context.clock.now_utc(),
            },
        )
        return True

    async def execute_claimed_resume(
        self,
        context: AgentExecutionContext,
        *,
        plan: ResumePlan,
        consumed: ResumeClaimConsumeResult,
    ) -> AgentExecutionResult:
        """Activate one F3-consumed claim and continue its Agent execution.

        Direct callers retain the R7-F4 behavior. The canonical R7-G wire
        path runs prepare_claimed_resume_activation inside a supervisor-owned
        task and waits for that readiness barrier before ACK.
        """

        resumed_results = await self.prepare_claimed_resume_activation(
            context,
            plan=plan,
            consumed=consumed,
        )
        return await self.execute(
            context,
            durable_revision=consumed.consumed_execution_revision,
            initial_tool_results=resumed_results,
        )

    async def _persist_tool_call(self, request: ToolExecutionRequest, iteration_id: str) -> None:
        if self._durable_store is None:
            return
        await self._durable_store.save_tool_call(
            {
                "id": request.tool_call_id,
                "execution_id": request.execution_id,
                "iteration_id": iteration_id,
                "invocation_id": request.invocation_id,
                "tool_call_id": request.tool_call_id,
                "capability_id": request.capability_id,
                "arguments": request.arguments,
                "status": "PENDING",
                "extra_metadata": {
                    **request.metadata,
                    "connection_id": request.connection_id,
                },
            }
        )

    async def _persist_tool_result(
        self,
        result: ToolExecutionResult,
        iteration_id: str,
    ) -> None:
        if self._durable_store is None:
            return
        await self._durable_store.save_tool_result(
            {
                "id": f"{result.execution_id}:{result.tool_call_id}",
                "execution_id": result.execution_id,
                "iteration_id": iteration_id,
                "tool_call_id": result.tool_call_id,
                "invocation_id": result.invocation_id,
                "capability_id": result.capability_id,
                "success": result.success,
                "output": result.output,
                "error_code": result.error_code,
                "error_message": result.error_message,
                "retryable": result.retryable,
                "extra_metadata": result.metadata,
                "attempt": result.metadata.get("attempt", 1),
            }
        )

    def _has_execution_lifecycle_store(self) -> bool:
        return all(
            callable(getattr(self._durable_store, name, None))
            for name in (
                "load_execution",
                "save_execution",
                "compare_and_set_execution",
            )
        )

    def _has_normalized_waiting_authority(
        self,
        context: AgentExecutionContext,
    ) -> bool:
        """Return whether this execution can atomically publish R7 WAITING.

        Generic execution lifecycle CAS is deliberately insufficient: once R7
        is active, WAITING must never become visible without its normalized
        checkpoint and ordered pending-invocation snapshots.
        """
        if not self._has_execution_lifecycle_store():
            return False
        if self._uses_task_budget(context):
            return callable(
                getattr(
                    self._task_budget_service,
                    "finish_task_scoped_execution",
                    None,
                )
            )
        return callable(
            getattr(self._durable_store, "commit_waiting_checkpoint", None)
        )

    async def _begin_durable_execution(
        self,
        context: AgentExecutionContext,
    ) -> int | None:
        """Create a new execution or CAS-claim one durable WAITING execution."""
        if not self._has_execution_lifecycle_store():
            return None

        record = await self._durable_store.load_execution(context.execution_id)
        resume_remaining: float | None = None
        if record is None:
            new_values = {
                "id": context.execution_id,
                "session_id": context.session_id,
                "agent_id": context.agent_id,
                "task_id": context.task_id,
                "branch_id": context.branch_id,
                "parent_execution_id": context.parent_execution_id,
                "retry_of_execution_id": context.retry_of_execution_id,
                "base_execution_id": context.base_execution_id,
                "base_checkpoint_id": context.base_checkpoint_id,
                "correlation_id": context.correlation_id,
                "state": AgentExecutionState.CREATED.value,
                "wait_reason": None,
                "revision": 0,
                "remaining_active_budget_seconds": (
                    context.remaining_active_budget_seconds
                ),
                "wait_expires_at": None,
                "request": dict(context.input),
                "context_state": {
                    "request_id": context.request_id,
                    "parent_execution_id": context.parent_execution_id,
                    "workflow_id": context.workflow_id,
                    "metadata": dict(context.metadata),
                    "causation_id": context.causation_id,
                    "trace_id": context.trace_id,
                    "connection_id": context.connection_id,
                    "limits": context.limits.model_dump(mode="json"),
                },
            }
            if self._uses_task_budget(context):
                assert context.task_id is not None
                delegation = (
                    await self._task_budget_service.resolve_delegation_admission(
                        context.task_id,
                        parent_execution_id=context.parent_execution_id,
                        child_agent_id=context.agent_id,
                    )
                )
                if context.parent_execution_id is not None:
                    if delegation.branch_id is None:
                        raise ExecutionConflictError(
                            "Delegated execution has no durable TaskBranch."
                        )
                    if (
                        context.branch_id is not None
                        and context.branch_id != delegation.branch_id
                    ):
                        raise ExecutionConflictError(
                            "Delegated execution branch does not match durable "
                            "parent lineage."
                        )
                    context.branch_id = delegation.branch_id
                    new_values["branch_id"] = delegation.branch_id

                new_values.update(
                    {
                        "state": AgentExecutionState.RUNNING.value,
                        "revision": 1,
                        "started_at": context.clock.now_utc(),
                    }
                )
                if (
                    context.parent_execution_id is None
                    and context.branch_id is None
                ):
                    admission = await (
                        self._task_budget_service
                        .start_root_task_scoped_execution(
                            context.task_id,
                            execution_id=context.execution_id,
                            execution_values=new_values,
                        )
                    )
                    context.branch_id = admission.branch_id
                    return admission.execution_revision

                return await (
                    self._task_budget_service.start_task_scoped_execution(
                        context.task_id,
                        execution_id=context.execution_id,
                        execution_values=new_values,
                        delegation_depth=delegation.delegation_depth,
                    )
                )
            try:
                await self._durable_store.save_execution(new_values)
            except Exception as exc:
                # The primary key is the idempotent startup guard.  Convert a
                # concurrent insert loss into the lifecycle conflict contract.
                if await self._durable_store.load_execution(context.execution_id):
                    raise ExecutionConflictError(
                        f"AgentExecution already exists: {context.execution_id}"
                    ) from exc
                raise
            expected_revision = 0
            current_state = AgentExecutionState.CREATED
        else:
            current_state, _ = normalize_execution_waiting(
                getattr(record, "state"),
                getattr(record, "wait_reason", None),
            )
            if current_state is not AgentExecutionState.WAITING:
                raise ExecutionConflictError(
                    f"AgentExecution {context.execution_id} is {current_state.value}, "
                    "not resumable WAITING"
                )
            expected_revision = getattr(record, "revision", 0)
            if (
                context.resume_revision is not None
                and context.resume_revision != expected_revision
            ):
                raise ExecutionConflictError(
                    f"Stale AgentExecution revision: {context.execution_id}@"
                    f"{context.resume_revision}"
                )

            persisted_remaining = getattr(
                record,
                "remaining_active_budget_seconds",
                None,
            )
            if persisted_remaining is None:
                raise ExecutionResumeBudgetError(
                    "UNKNOWN_ACTIVE_BUDGET: legacy WAITING execution cannot "
                    "resume without a trusted remaining active duration."
                )

            resume_remaining = float(persisted_remaining)
            if not math.isfinite(resume_remaining):
                raise ExecutionResumeBudgetError(
                    "INVALID_ACTIVE_BUDGET: persisted active duration is "
                    "not finite."
                )

            now_utc = context.clock.now_utc()
            wait_expires_at = _utc_datetime(
                getattr(record, "wait_expires_at", None)
            )
            context.wait_expires_at = wait_expires_at

            if resume_remaining <= 0.0:
                await self._durable_store.compare_and_set_execution(
                    context.execution_id,
                    expected_revision,
                    {
                        "state": AgentExecutionState.TIMEOUT.value,
                        "wait_reason": None,
                        "wait_expires_at": None,
                        "remaining_active_budget_seconds": 0.0,
                        "error": "AGENT_EXECUTION_TIMEOUT",
                        "completed_at": now_utc,
                    },
                )
                raise ExecutionResumeBudgetError(
                    "AGENT_EXECUTION_TIMEOUT: no active budget remains."
                )

            if wait_expires_at is not None and now_utc >= wait_expires_at:
                await self._durable_store.compare_and_set_execution(
                    context.execution_id,
                    expected_revision,
                    {
                        "state": AgentExecutionState.TIMEOUT.value,
                        "wait_reason": None,
                        "wait_expires_at": None,
                        "remaining_active_budget_seconds": resume_remaining,
                        "error": "WAIT_TTL_EXPIRED",
                        "completed_at": now_utc,
                    },
                )
                raise ExecutionWaitExpiredError(
                    "WAIT_TTL_EXPIRED: durable WAITING execution expired."
                )

        if not AgentExecutionStateMachine.can_transition(
            current_state,
            AgentExecutionState.RUNNING,
        ):
            raise ExecutionConflictError(
                f"Cannot start AgentExecution from {current_state.value}"
            )
        start_values = {
            "state": AgentExecutionState.RUNNING.value,
            "wait_reason": None,
            "wait_expires_at": None,
            "started_at": context.clock.now_utc(),
        }
        if (
            current_state is AgentExecutionState.WAITING
            and self._uses_task_budget(context)
        ):
            assert context.task_id is not None
            await self._task_budget_service.resume_task_scoped_execution(
                context.task_id,
                execution_id=context.execution_id,
                source_revision=expected_revision,
                transition_values=start_values,
                delegated=context.parent_execution_id is not None,
            )
        else:
            await self._durable_store.compare_and_set_execution(
                context.execution_id,
                expected_revision,
                start_values,
            )

        if current_state is AgentExecutionState.WAITING:
            assert resume_remaining is not None
            context.remaining_active_budget_seconds = resume_remaining
            context.wait_expires_at = None
            context.restore_active_budget(resume_remaining)

        return expected_revision + 1

    async def _cancel_durable_revision(
        self,
        context: AgentExecutionContext,
        revision: int | None,
        *,
        error_message: str,
    ) -> None:
        if revision is None or not self._has_execution_lifecycle_store():
            return
        await self._transition_running_durable(
            context,
            revision,
            {
                "state": AgentExecutionState.CANCELLED.value,
                "wait_reason": None,
                "wait_expires_at": None,
                "error": error_message,
                "completed_at": context.clock.now_utc(),
            },
        )

    async def cancel_claimed_execution(
        self,
        context: AgentExecutionContext,
        revision: int,
        *,
        error_message: str = "RESUME_ACTIVATION_FAILED",
    ) -> None:
        """Fail closed when a claimed RUNNING resume cannot acquire an owner."""
        await self._cancel_durable_revision(
            context,
            revision,
            error_message=error_message,
        )

    async def _begin_durable_execution_owned(
        self,
        context: AgentExecutionContext,
    ) -> int | None:
        """Own the durable begin Task across outer coroutine cancellation."""
        begin_task = asyncio.create_task(
            self._begin_durable_execution(context),
            name=f"agent-begin:{context.execution_id}",
        )
        try:
            return await asyncio.shield(begin_task)
        except asyncio.CancelledError:
            outcome = await asyncio.gather(
                begin_task,
                return_exceptions=True,
            )
            revision = outcome[0]
            if isinstance(revision, int) and not isinstance(revision, bool):
                await self._cancel_durable_revision(
                    context,
                    revision,
                    error_message=(
                        "Agent execution cancelled during durable begin."
                    ),
                )
            raise

    async def claim_resume(self, context: AgentExecutionContext) -> int:
        """Synchronously claim one durable WAITING execution before WS ACK."""
        revision = await self._begin_durable_execution_owned(context)
        if revision is None:
            raise RuntimeError(
                "Durable resume requires an AgentExecution lifecycle store."
            )
        return revision

    async def _finish_durable_execution(
        self,
        context: AgentExecutionContext,
        result: AgentExecutionResult,
        expected_revision: int | None,
    ) -> AgentExecutionResult:
        waiting_attempt = result.state is AgentLoopState.WAITING
        if waiting_attempt:
            target = AgentExecutionState.WAITING
            reason = result.wait_reason
            AgentExecutionStateMachine.validate_state(target, reason)

            remaining = context.freeze_active_budget()
            if remaining is None or remaining <= 0.0:
                target = AgentExecutionState.TIMEOUT
                reason = None
                context.wait_expires_at = None
                result = result.model_copy(
                    update={
                        "state": AgentLoopState.TIMEOUT,
                        "wait_reason": None,
                        "error_code": "AGENT_EXECUTION_TIMEOUT",
                        "error_message": (
                            "Agent execution active budget exhausted "
                            "before WAITING."
                        ),
                        "checkpoint_id": None,
                    }
                )
            else:
                assert reason is not None
                ttl_seconds = self._wait_policy.wait_ttl_seconds(
                    reason=reason,
                    context=context,
                )
                context.wait_expires_at = (
                    None
                    if ttl_seconds is None
                    else context.clock.now_utc()
                    + timedelta(seconds=ttl_seconds)
                )
        else:
            context.clear_iteration_budget()
            target = AgentExecutionState(result.state.value)
            reason = None
            context.wait_expires_at = None

        AgentExecutionStateMachine.validate_state(target, reason)
        if not AgentExecutionStateMachine.can_transition(
            AgentExecutionState.RUNNING,
            target,
        ):
            raise ExecutionConflictError(
                f"Invalid durable completion RUNNING -> {target.value}"
            )
        if expected_revision is None:
            return result

        values = {
            "state": target.value,
            "wait_reason": reason.value if reason is not None else None,
            "wait_expires_at": context.wait_expires_at,
            "result": result.model_dump(mode="json"),
            "error": result.error_message,
            "completed_at": (
                None
                if target is AgentExecutionState.WAITING
                else context.clock.now_utc()
            ),
        }
        if waiting_attempt:
            values["remaining_active_budget_seconds"] = (
                context.remaining_active_budget_seconds
            )

        checkpoint_values = None
        pending_invocations: Sequence[dict[str, Any]] = ()
        if target is AgentExecutionState.WAITING:
            checkpoint_id = (
                f"{context.execution_id}:checkpoint:{expected_revision + 1}"
            )
            checkpoint_values = {
                "checkpoint_id": checkpoint_id,
                "execution_id": context.execution_id,
                "execution_revision": expected_revision + 1,
                "session_id": context.session_id,
                "task_id": context.task_id,
                "branch_id": context.branch_id,
                "iteration": context.iteration,
                "wait_reason": reason.value if reason is not None else "",
                "remaining_active_budget_seconds": (
                    context.remaining_active_budget_seconds
                ),
                "wait_expires_at": context.wait_expires_at,
                "origin_client_id": context.metadata.get("client_id"),
                "origin_connection_id": context.waiting_origin_connection_id,
                "transcript_snapshot": context.waiting_checkpoint_transcript,
                "metadata_json": {
                    "request_id": context.request_id,
                    "correlation_id": context.correlation_id,
                    "trace_id": context.trace_id,
                },
            }
            pending_invocations = tuple(context.waiting_pending_invocations)
            result = result.model_copy(
                update={"checkpoint_id": checkpoint_id}
            )
            values["result"] = result.model_dump(mode="json")

        await self._transition_running_durable(
            context,
            expected_revision,
            values,
            checkpoint_values=checkpoint_values,
            pending_invocations=pending_invocations,
        )
        if target is AgentExecutionState.WAITING:
            context.waiting_checkpoint_transcript = []
            context.waiting_pending_invocations = []
            context.waiting_origin_connection_id = None
        return result

    async def execute(
        self,
        context: AgentExecutionContext,
        *,
        durable_revision: int | None = None,
        initial_tool_results: Sequence[ToolExecutionResult] = (),
    ) -> AgentExecutionResult:
        """Run exactly one durable AgentExecution lifecycle."""
        revision = (
            durable_revision
            if durable_revision is not None
            else await self._begin_durable_execution_owned(context)
        )
        try:
            result = await self._execute_loop(
                context,
                initial_tool_results=initial_tool_results,
            )
        except asyncio.CancelledError:
            await self._cancel_durable_revision(
                context,
                revision,
                error_message="Agent execution cancelled.",
            )
            raise
        except Exception as exc:
            if revision is not None:
                await self._transition_running_durable(
                    context,
                    revision,
                    {
                        "state": AgentExecutionState.FAILED.value,
                        "wait_reason": None,
                        "wait_expires_at": None,
                        "error": str(exc),
                        "completed_at": datetime.now(timezone.utc),
                    },
                )
            raise
        result = await self._finish_durable_execution(
            context,
            result,
            revision,
        )
        return result

    async def _execute_loop(
        self,
        context: AgentExecutionContext,
        *,
        initial_tool_results: Sequence[ToolExecutionResult] = (),
    ) -> AgentExecutionResult:
        """Execute one agent until a final answer or terminal failure."""
        # The runtime should persist each iteration and tool checkpoint before
        # continuing the loop, then resume from the last durable checkpoint.
        iterations: list[AgentIteration] = []
        context.validate_context_seed()
        if context.branch_base_transcript is not None:
            transcript = [
                InferenceMessage.model_validate(item)
                for item in context.branch_base_transcript
            ]
            history_mode = AgentContextHistoryMode.EXPLICIT
        elif context.resume_revision is not None:
            transcript = [
                InferenceMessage.model_validate(item)
                for item in context.resume_transcript
            ]
            history_mode = AgentContextHistoryMode.EXPLICIT
        else:
            transcript = []
            history_mode = AgentContextHistoryMode.AUTO
        latest_tool_results: tuple[ToolExecutionResult, ...] = tuple(
            initial_tool_results
        )
        total_usage = context.usage

        await self._publish(AgentEventName.EXECUTION_STARTED, context)

        if context.resume_pending_tool_calls:
            context.begin_iteration_budget()
            try:
                latest_tool_results = await self._execute_resumed_tool_calls(
                    context
                )
                transcript.extend(
                    _tool_results_to_messages(latest_tool_results)
                )
                context.resume_pending_tool_calls = []
                await self._persist_execution_checkpoint(
                    context,
                    transcript,
                )
            finally:
                context.clear_iteration_budget()

        if self._execution_policy.check_start(context) is not PolicyDecision.ALLOW:
            rejected_state = (
                AgentLoopState.CANCELLED
                if context.cancelled
                else AgentLoopState.TIMEOUT
                if context.timed_out
                else AgentLoopState.FAILED
            )
            await self._publish(
                {
                    AgentLoopState.CANCELLED: AgentEventName.EXECUTION_CANCELLED,
                    AgentLoopState.TIMEOUT: AgentEventName.EXECUTION_TIMEOUT,
                    AgentLoopState.FAILED: AgentEventName.EXECUTION_FAILED,
                }[rejected_state],
                context,
                payload={"error_code": "AGENT_EXECUTION_NOT_ALLOWED"},
            )
            return AgentExecutionResult(
                execution_id=context.execution_id,
                agent_id=context.agent_id,
                state=(
                    AgentLoopState.CANCELLED
                    if context.cancelled
                    else AgentLoopState.TIMEOUT
                    if context.timed_out
                    else AgentLoopState.FAILED
                ),
                iterations=(),
                usage=total_usage,
                error_code="AGENT_EXECUTION_NOT_ALLOWED",
                error_message="Agent execution was rejected by execution policy.",
            )

        for iteration_number in range(
            context.iteration + 1,
            context.limits.max_iterations + 1,
        ):
            try:
                context.ensure_active()
                context.next_iteration()
                if context.begin_iteration_budget() <= 0.0:
                    raise TimeoutError(
                        "Agent iteration deadline exceeded before iteration start."
                    )

                if (
                    self._execution_policy.check_iteration(context, iteration_number)
                    is not PolicyDecision.ALLOW
                ):
                    await self._publish(
                        AgentEventName.EXECUTION_FAILED,
                        context,
                        payload={"error_code": "MAX_ITERATIONS_EXCEEDED"},
                    )
                    return self._terminal_result(
                        context,
                        iterations,
                        total_usage,
                        AgentLoopState.FAILED,
                        "MAX_ITERATIONS_EXCEEDED",
                        "Agent iteration limit exceeded.",
                        last_tool_results=latest_tool_results,
                    )

                record = AgentIteration(
                    execution_id=context.execution_id,
                    iteration=iteration_number,
                    state=AgentLoopState.PREPARING,
                )
                iterations.append(record)
                await self._publish(
                    AgentEventName.ITERATION_STARTED,
                    context,
                    iteration=iteration_number,
                )
                await self._persist_iteration(record)
                record.state = transition(record.state, AgentLoopState.THINKING)

                snapshot = await self._await_contextual(
                    self._context_builder.build(
                        context,
                        AgentContextRequest(
                            execution_id=context.execution_id,
                            iteration=iteration_number,
                            prior_messages=[
                                message.model_dump(mode="json")
                                for message in transcript
                            ],
                            history_mode=history_mode,
                            # Tool results already live in transcript. Keeping
                            # this empty avoids duplication by ContextBuilderAdapter.
                            tool_results=[],
                        ),
                    ),
                    context=context,
                    timeout_seconds=context.remaining_iteration_seconds,
                )

                # The first snapshot contains the authoritative session/system
                # history. Seed the canonical transcript exactly once.
                if (
                    not transcript
                    and history_mode is AgentContextHistoryMode.AUTO
                ):
                    transcript.extend(snapshot.messages)

                request_id = f"inf_{uuid.uuid4().hex}"
                record.inference_request_id = request_id
                await self._persist_iteration(record)
                inference_timeout = context.remaining_for_operation(
                    getattr(
                        context.limits,
                        "inference_timeout_seconds",
                        None,
                    )
                )
                if inference_timeout <= 0:
                    raise TimeoutError(
                        "Agent execution deadline exceeded before inference."
                    )

                await self._publish(
                    AgentEventName.INFERENCE_REQUESTED,
                    context,
                    iteration=iteration_number,
                    request_id=request_id,
                    payload={
                        "model": getattr(context.agent, "model", None)
                        or context.metadata.get("model")
                    },
                )

                if self._uses_task_budget(context):
                    assert context.task_id is not None
                    await self._task_budget_service.reserve_inference(
                        context.task_id,
                        request_id=request_id,
                    )

                response = await self._inference.complete(
                    InferenceRequest(
                        request_id=request_id,
                        execution_id=context.execution_id,
                        iteration=iteration_number,
                        messages=list(snapshot.messages),
                        tools=list(snapshot.tools),
                        model=(
                            getattr(context.agent, "model", None)
                            or context.metadata.get("model")
                        ),
                        timeout_seconds=inference_timeout,
                        cancellation_event=context.cancellation_event,
                        metadata=dict(snapshot.metadata),
                    )
                )

                if self._uses_task_budget(context):
                    assert context.task_id is not None
                    await self._task_budget_service.account_usage(
                        context.task_id,
                        usage_key=request_id,
                        tokens=response.usage.total_tokens,
                        cost_usd=response.usage.estimated_cost_usd,
                    )

                await self._publish(
                    AgentEventName.INFERENCE_COMPLETED,
                    context,
                    iteration=iteration_number,
                    request_id=request_id,
                    payload={
                        "finish_reason": response.finish_reason,
                        "provider": response.provider,
                        "model": response.model,
                    },
                )

                transcript.append(response.message)
                total_usage = _add_usage(total_usage, response.usage)
                context.usage = total_usage

                await self._persist_execution_checkpoint(
                    context,
                    transcript,
                    inference_request=InferenceRequest(
                        request_id=request_id,
                        execution_id=context.execution_id,
                        iteration=iteration_number,
                        messages=list(snapshot.messages),
                        tools=list(snapshot.tools),
                        model=getattr(context.agent, "model", None),
                        timeout_seconds=inference_timeout,
                        cancellation_event=None,
                        metadata=dict(snapshot.metadata),
                    ),
                    inference_response=response,
                )

                if not response.message.tool_calls:
                    record.close(AgentLoopState.FINALIZING)
                    record.close(AgentLoopState.COMPLETED)
                    await self._persist_iteration(record)
                    await self._publish(
                        AgentEventName.ITERATION_COMPLETED,
                        context,
                        iteration=iteration_number,
                        payload={"state": record.state.value},
                    )
                    await self._publish(
                        AgentEventName.EXECUTION_COMPLETED,
                        context,
                        payload={"state": AgentLoopState.COMPLETED.value},
                    )
                    return AgentExecutionResult(
                        execution_id=context.execution_id,
                        agent_id=context.agent_id,
                        state=AgentLoopState.COMPLETED,
                        output=_extract_text(response.message.content),
                        final_message=response.message,
                        iterations=tuple(iterations),
                        last_tool_results=latest_tool_results,
                        usage=context.usage,
                    )

                record.state = transition(record.state, AgentLoopState.TOOL_CALLING)
                tool_requests = [
                    ToolExecutionRequest(
                        execution_id=context.execution_id,
                        iteration=iteration_number,
                        invocation_id=f"inv_{uuid.uuid4().hex}",
                        tool_call_id=tool_call.id,
                        capability_id=tool_call.name,
                        connection_id=context.connection_id,
                        arguments=dict(tool_call.arguments),
                    )
                    for tool_call in response.message.tool_calls
                ]
                record.tool_call_ids = [item.tool_call_id for item in tool_requests]

                record.state = transition(record.state, AgentLoopState.WAITING_TOOL)
                await self._persist_iteration(record)
                iteration_id = f"{record.execution_id}:iteration:{record.iteration}"
                for request in tool_requests:
                    await self._publish(
                        AgentEventName.TOOL_REQUESTED,
                        context,
                        iteration=iteration_number,
                        tool_call_id=request.tool_call_id,
                        invocation_id=request.invocation_id,
                        payload={"capability_id": request.capability_id},
                    )
                    await self._publish(
                        AgentEventName.TOOL_STARTED,
                        context,
                        iteration=iteration_number,
                        tool_call_id=request.tool_call_id,
                        invocation_id=request.invocation_id,
                        payload={"capability_id": request.capability_id},
                    )
                    await self._persist_tool_call(request, iteration_id)
                await self._reserve_task_tool_calls(
                    context,
                    tool_requests,
                )
                try:
                    raw_tool_results = await self._await_contextual(
                        self._tool_execution.execute_many(
                            context,
                            tool_requests,
                            max_parallel=context.limits.max_parallel_tools,
                        ),
                        context=context,
                        timeout_seconds=context.remaining_iteration_seconds,
                    )
                except (asyncio.CancelledError, TimeoutError) as exc:
                    error_code = (
                        "CAPABILITY_CANCELLED"
                        if isinstance(exc, asyncio.CancelledError)
                        else "CAPABILITY_TIMEOUT"
                    )
                    for request in tool_requests:
                        await self._publish(
                            AgentEventName.TOOL_FAILED,
                            context,
                            iteration=iteration_number,
                            tool_call_id=request.tool_call_id,
                            invocation_id=request.invocation_id,
                            payload={
                                "capability_id": request.capability_id,
                                "error_code": error_code,
                                "error_message": str(exc),
                            },
                        )
                    raise
                except Exception as exc:
                    for request in tool_requests:
                        await self._publish(
                            AgentEventName.TOOL_FAILED,
                            context,
                            iteration=iteration_number,
                            tool_call_id=request.tool_call_id,
                            invocation_id=request.invocation_id,
                            payload={
                                "capability_id": request.capability_id,
                                "error_code": getattr(exc, "code", type(exc).__name__),
                                "error_message": str(exc),
                            },
                        )
                    raise
                latest_tool_results = tuple(
                    _order_tool_results(tool_requests, raw_tool_results)
                )
                committed_batch: list[ToolExecutionResult] = []
                uncommitted_tool_call_ids: set[str] = set()
                for request, result in zip(tool_requests, latest_tool_results):
                    await self._persist_tool_result(result, iteration_id)
                    if self._durable_store is None:
                        committed_batch.append(result)
                    else:
                        committed_result = await self._load_committed_tool_result(
                            request
                        )
                        if committed_result is None:
                            uncommitted_tool_call_ids.add(result.tool_call_id)
                        else:
                            committed_batch.append(committed_result)
                    await self._publish(
                        AgentEventName.TOOL_COMPLETED
                        if result.success
                        else AgentEventName.TOOL_FAILED,
                        context,
                        iteration=iteration_number,
                        tool_call_id=result.tool_call_id,
                        invocation_id=result.invocation_id,
                        payload={
                            "capability_id": result.capability_id,
                            "error_code": result.error_code,
                            "error_message": result.error_message,
                        },
                    )

                commitment_aware = callable(
                    getattr(
                        self._durable_store,
                        "load_committed_tool_result",
                        None,
                    )
                )
                if commitment_aware:
                    remote_waiting = [
                        item
                        for item in latest_tool_results
                        if item.tool_call_id in uncommitted_tool_call_ids
                    ]
                else:
                    remote_waiting = [
                        item
                        for item in latest_tool_results
                        if (
                            item.tool_call_id in uncommitted_tool_call_ids
                            or item.error_code
                            in {
                                "REMOTE_CONNECTION_LOST",
                                "REMOTE_OUTCOME_UNKNOWN",
                                "REMOTE_RESULT_RECONCILIATION_REQUIRED",
                            }
                            or item.metadata.get("original_error_code")
                            == "REMOTE_CONNECTION_LOST"
                        )
                    ]
                if remote_waiting:
                    lost = remote_waiting[0]
                    old_connection_id = (
                        lost.metadata.get("connection_id")
                        or context.connection_id
                        or ""
                    )
                    context.waiting_checkpoint_transcript = [
                        item.model_dump(mode="json") for item in transcript
                    ]
                    context.waiting_pending_invocations = [
                        {
                            "ordinal": ordinal,
                            "invocation_id": item.invocation_id,
                            "tool_call_id": item.tool_call_id,
                            "capability_id": item.capability_id,
                        }
                        for ordinal, item in enumerate(latest_tool_results)
                        if item in remote_waiting
                    ]
                    context.waiting_origin_connection_id = old_connection_id
                    context.connection_id = None

                    # R7-B normalized WAITING is the canonical production
                    # authority. The Phase 6.9 continuation service remains
                    # only for compatibility stores without durable lifecycle
                    # primitives.
                    if self._has_execution_lifecycle_store():
                        if not self._has_normalized_waiting_authority(context):
                            raise ExecutionConflictError(
                                "NORMALIZED_WAITING_AUTHORITY_UNAVAILABLE: "
                                "durable execution lifecycle cannot publish "
                                "WAITING without the canonical R7 checkpoint "
                                "transaction."
                            )
                        record.close(
                            AgentLoopState.FAILED,
                            error_code="WAITING_FOR_CONNECTION",
                        )
                        await self._persist_iteration(record)
                        return AgentExecutionResult(
                            execution_id=context.execution_id,
                            agent_id=context.agent_id,
                            state=AgentLoopState.WAITING,
                            wait_reason=AgentExecutionWaitReason.CONNECTION,
                            iterations=tuple(iterations),
                            last_tool_results=latest_tool_results,
                            usage=context.usage,
                            error_code="WAITING_FOR_CONNECTION",
                            error_message=(
                                "Remote capability requires a new connection."
                            ),
                            checkpoint_id=None,
                        )

                    raise ExecutionConflictError(
                        "PROVISIONAL tool result cannot enter model context "
                        "without a durable WAITING checkpoint."
                    )

                latest_tool_results = tuple(
                    _order_tool_results(tool_requests, committed_batch)
                )

                # ToolExecutionAdapter may update context.usage with per-tool
                # accounting. Context is authoritative after the tool batch.
                total_usage = context.usage
                record.close(AgentLoopState.THINKING)
                await self._persist_iteration(record)
                await self._publish(
                    AgentEventName.ITERATION_COMPLETED,
                    context,
                    iteration=iteration_number,
                    payload={"state": record.state.value},
                )

                transcript.extend(_tool_results_to_messages(latest_tool_results))
                await self._persist_execution_checkpoint(context, transcript)
                context.clear_iteration_budget()

            except asyncio.CancelledError:
                record = iterations[-1] if iterations else None
                if record is not None and record.state not in {
                    AgentLoopState.COMPLETED,
                    AgentLoopState.CANCELLED,
                    AgentLoopState.TIMEOUT,
                }:
                    record.close(
                        AgentLoopState.CANCELLED,
                        error_code="AGENT_CANCELLED",
                    )
                    await self._persist_iteration(record)
                    await self._publish(
                        AgentEventName.ITERATION_COMPLETED,
                        context,
                        iteration=record.iteration,
                        payload={"state": record.state.value},
                    )
                    await self._publish(
                        AgentEventName.EXECUTION_CANCELLED,
                        context,
                        payload={"error_code": "AGENT_CANCELLED"},
                    )
                return self._terminal_result(
                    context,
                    iterations,
                    context.usage,
                    AgentLoopState.CANCELLED,
                    "AGENT_CANCELLED",
                    "Agent execution cancelled.",
                    last_tool_results=latest_tool_results,
                )
            except (asyncio.TimeoutError, TimeoutError):
                record = iterations[-1] if iterations else None
                if record is not None and record.state not in {
                    AgentLoopState.COMPLETED,
                    AgentLoopState.CANCELLED,
                    AgentLoopState.TIMEOUT,
                }:
                    record.close(
                        AgentLoopState.TIMEOUT,
                        error_code="AGENT_TIMEOUT",
                    )
                    await self._persist_iteration(record)
                    await self._publish(
                        AgentEventName.ITERATION_COMPLETED,
                        context,
                        iteration=record.iteration,
                        payload={"state": record.state.value},
                    )
                    await self._publish(
                        AgentEventName.EXECUTION_TIMEOUT,
                        context,
                        payload={"error_code": "AGENT_TIMEOUT"},
                    )
                return self._terminal_result(
                    context,
                    iterations,
                    context.usage,
                    AgentLoopState.TIMEOUT,
                    "AGENT_TIMEOUT",
                    "Agent execution timed out.",
                    last_tool_results=latest_tool_results,
                )
            except Exception as exc:
                error_code = (
                    getattr(exc, "code", None)
                    or getattr(exc, "error_code", None)
                    or type(exc).__name__
                )
                failure_domain = getattr(exc, "failure_domain", None) or "AGENT"
                retryable = bool(getattr(exc, "retryable", False))
                record = iterations[-1] if iterations else None
                if record is not None and record.state not in {
                    AgentLoopState.COMPLETED,
                    AgentLoopState.CANCELLED,
                    AgentLoopState.TIMEOUT,
                }:
                    record.close(
                        AgentLoopState.FAILED,
                        error_code=error_code,
                    )
                    await self._persist_iteration(record)
                    await self._publish(
                        AgentEventName.ITERATION_COMPLETED,
                        context,
                        iteration=record.iteration,
                        payload={"state": record.state.value},
                    )
                    await self._publish(
                        AgentEventName.EXECUTION_FAILED,
                        context,
                        payload={
                            "error_code": error_code,
                            "failure_domain": failure_domain,
                            "retryable": retryable,
                        },
                    )
                return self._terminal_result(
                    context,
                    iterations,
                    context.usage,
                    AgentLoopState.FAILED,
                    error_code,
                    str(exc),
                    last_tool_results=latest_tool_results,
                    failure_domain=failure_domain,
                    retryable=retryable,
                )

        await self._publish(
            AgentEventName.EXECUTION_FAILED,
            context,
            payload={"error_code": "MAX_ITERATIONS_EXCEEDED"},
        )
        return self._terminal_result(
            context,
            iterations,
            context.usage,
            AgentLoopState.FAILED,
            "MAX_ITERATIONS_EXCEEDED",
            "Agent iteration limit exceeded.",
            last_tool_results=latest_tool_results,
        )

    @staticmethod
    async def _await_contextual(
        awaitable,
        *,
        context: AgentExecutionContext,
        timeout_seconds: float | None,
    ):
        """Bound any port call by the execution deadline and cancellation event."""
        task = asyncio.create_task(awaitable)
        cancel_task = asyncio.create_task(context.cancellation_event.wait())
        try:
            if context.cancelled:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise asyncio.CancelledError()

            done, _ = await asyncio.wait(
                {task, cancel_task},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return await task
            if cancel_task in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise asyncio.CancelledError()

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise asyncio.TimeoutError(
                "Agent execution deadline exceeded."
            )
        except BaseException:
            if not task.done():
                task.cancel()
            await asyncio.gather(
                task,
                return_exceptions=True,
            )
            raise
        finally:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    @staticmethod
    def _terminal_result(
        context: AgentExecutionContext,
        iterations: Sequence[AgentIteration],
        usage,
        state: AgentLoopState,
        error_code: str,
        error_message: str,
        *,
        last_tool_results: Sequence[ToolExecutionResult] = (),
        failure_domain: str = "AGENT",
        retryable: bool = False,
    ) -> AgentExecutionResult:
        return AgentExecutionResult(
            execution_id=context.execution_id,
            agent_id=context.agent_id,
            state=state,
            iterations=tuple(iterations),
            last_tool_results=tuple(last_tool_results),
            usage=usage,
            error_code=error_code,
            error_message=error_message,
            failure_domain=failure_domain,
            retryable=retryable,
        )


def _tool_results_to_messages(
    results: Sequence[ToolExecutionResult],
) -> list[InferenceMessage]:
    return [
        InferenceMessage(
            role="tool",
            name=result.capability_id,
            tool_call_id=result.tool_call_id,
            content=(
                result.output
                if result.success
                else {
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                }
            ),
            metadata={
                "success": result.success,
                "retryable": result.retryable,
            },
        )
        for result in results
    ]

def _order_tool_results(
    requests: Sequence[ToolExecutionRequest],
    results: Sequence[ToolExecutionResult],
) -> list[ToolExecutionResult]:
    """Normalize tool result order to the model's original tool-call order."""
    by_id: dict[str, ToolExecutionResult] = {}
    for result in results:
        if result.tool_call_id in by_id:
            raise ValueError(
                f"Duplicate tool result for tool_call_id={result.tool_call_id!r}."
            )
        by_id[result.tool_call_id] = result

    ordered: list[ToolExecutionResult] = []
    for request in requests:
        result = by_id.get(request.tool_call_id)
        if result is None:
            raise ValueError(
                f"Missing tool result for tool_call_id={request.tool_call_id!r}."
            )
        ordered.append(result)
    if len(ordered) != len(results):
        raise ValueError("Tool execution returned an unexpected result count.")
    return ordered


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and item.get("text"):
                parts.append(str(item["text"]))
        return "".join(parts)
    return str(content or "")


def _add_usage(left, right):
    data = left.model_dump()
    incoming = right.model_dump()
    for key, value in incoming.items():
        if isinstance(value, (int, float)):
            data[key] = data.get(key, 0) + value
    return type(left).model_validate(data)