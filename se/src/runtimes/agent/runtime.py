from __future__ import annotations

import asyncio
import json
import math
from time import monotonic
import structlog
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ...domain.schemas.agent_execution import (
    AgentExecutionState,
    AgentExecutionWaitReason,
    normalize_execution_waiting,
)
from ...provider.exceptions import (
    ProviderDeadlineExceededError,
    ProviderRecoveryAuthorityLostError,
    ProviderTimeoutError,
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
from .contracts.recovery import (
    RecoveryActivationResult,
    RecoveryInferenceDisposition,
    RecoveryPlan,
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


logger = structlog.get_logger(__name__)


class ExecutionWaitExpiredError(ExecutionConflictError):
    """A durable WAITING execution reached its wall-clock expiry."""


class ExecutionResumeBudgetError(ExecutionConflictError):
    """A durable WAITING execution has no resumable active-time budget."""


class RecoveryProgressionDeferredError(ExecutionConflictError):
    """Bounded R12-F3-B progression intentionally stopped fail-closed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


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
        f7t_canonicalizer=None,
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
        self._f7t_canonicalizer = f7t_canonicalizer
        self._f7t_publication_tasks: set[asyncio.Task] = set()
        self._f7t_publication_accepting = f7t_canonicalizer is not None

    def _observe_f7t_publication_task(self, completed: asyncio.Task) -> None:
        self._f7t_publication_tasks.discard(completed)
        if completed.cancelled():
            return
        error = completed.exception()
        if error is not None:
            logger.warning(
                "cas_f7t_publication_failed",
                task_name=completed.get_name(),
                error_type=type(error).__name__,
            )

    def _schedule_f7t_publication_for_committed_result(
        self,
        *,
        source_result_id: str,
    ) -> None:
        if (
            self._f7t_canonicalizer is None
            or not self._f7t_publication_accepting
        ):
            return
        task = asyncio.create_task(
            self._f7t_canonicalizer.canonicalize_committed_result(
                source_result_id=source_result_id,
            ),
            name=f"cas-f7t-publication:{source_result_id}",
        )
        self._f7t_publication_tasks.add(task)
        task.add_done_callback(self._observe_f7t_publication_task)

    async def quiesce_f7t_publication(self) -> None:
        """Stop new F7-T scheduling and drain owned publication tasks."""

        self._f7t_publication_accepting = False
        tasks = tuple(self._f7t_publication_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._f7t_publication_tasks.clear()

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
        recovery_fence: Mapping[str, Any] | None = None,
        expected_task_budget_incarnation_generation: int | None = None,
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
                    recovery_fence=recovery_fence,
                    expected_incarnation_generation=(
                        expected_task_budget_incarnation_generation
                    ),
                )
            )
            if recovery_fence is None:
                reconciler = getattr(
                    self._task_budget_service,
                    "reconcile_multibranch_task_activity",
                    None,
                )
                if callable(reconciler):
                    await reconciler(context.task_id)
            return target_revision
        if checkpoint_values is not None:
            if recovery_fence is not None:
                raise ExecutionConflictError(
                    "RECOVERY_WAITING_TRANSITION_UNSUPPORTED: "
                    "F3-B does not publish a new WAITING recovery cut."
                )
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
        if recovery_fence is None:
            await self._durable_store.compare_and_set_execution(
                context.execution_id,
                revision,
                values,
            )
        else:
            await self._durable_store.compare_and_set_execution(
                context.execution_id,
                revision,
                values,
                recovery_fence=recovery_fence,
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

    async def _persist_iteration(
        self,
        record: AgentIteration,
        *,
        recovery_fence: Mapping[str, Any] | None = None,
    ) -> None:
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
            if recovery_fence is None:
                await self._durable_store.save_iteration(values)
            else:
                await self._durable_store.save_iteration(
                    values,
                    recovery_fence=recovery_fence,
                )
        else:
            if recovery_fence is None:
                await self._durable_store.update_iteration(
                    existing.id,
                    values,
                )
            else:
                await self._durable_store.update_iteration(
                    existing.id,
                    values,
                    recovery_fence=recovery_fence,
                )

    async def _persist_execution_checkpoint(
        self,
        context: AgentExecutionContext,
        transcript: Sequence[InferenceMessage],
        *,
        inference_request: InferenceRequest | None = None,
        inference_response=None,
        recovery_fence: Mapping[str, Any] | None = None,
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
        if recovery_fence is None:
            await self._durable_store.update_checkpoint(
                context.execution_id,
                values,
            )
        else:
            await self._durable_store.update_checkpoint(
                context.execution_id,
                values,
                recovery_fence=recovery_fence,
            )

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
        if getattr(record, "commit_state", "PROVISIONAL") != "COMMITTED":
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
        source_result_id = getattr(record, "id", None)
        if isinstance(source_result_id, str) and source_result_id:
            self._schedule_f7t_publication_for_committed_result(
                source_result_id=source_result_id,
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
        source_result_id = getattr(record, "id", None)
        if isinstance(source_result_id, str) and source_result_id:
            self._schedule_f7t_publication_for_committed_result(
                source_result_id=source_result_id,
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
                timeout_values = {
                    "state": AgentExecutionState.TIMEOUT.value,
                    "wait_reason": None,
                    "wait_expires_at": None,
                    "remaining_active_budget_seconds": 0.0,
                    "error": "AGENT_EXECUTION_TIMEOUT",
                    "completed_at": now_utc,
                }
                if (
                    self._uses_task_budget(context)
                    and self._task_budget_service is not None
                ):
                    assert context.task_id is not None
                    timeout_revision = (
                        await self._task_budget_service.expire_task_scoped_waiting_execution(
                            context.task_id,
                            execution_id=context.execution_id,
                            source_revision=expected_revision,
                            transition_values=timeout_values,
                        )
                    )
                    if timeout_revision is None:
                        raise ExecutionConflictError(
                            "Stale AgentExecution revision/state while applying "
                            f"task-scoped timeout: {context.execution_id}@"
                            f"{expected_revision}"
                        )
                else:
                    await self._durable_store.compare_and_set_execution(
                        context.execution_id,
                        expected_revision,
                        timeout_values,
                    )
                raise ExecutionResumeBudgetError(
                    "AGENT_EXECUTION_TIMEOUT: no active budget remains."
                )

            if wait_expires_at is not None and now_utc >= wait_expires_at:
                timeout_values = {
                    "state": AgentExecutionState.TIMEOUT.value,
                    "wait_reason": None,
                    "wait_expires_at": None,
                    "remaining_active_budget_seconds": resume_remaining,
                    "error": "WAIT_TTL_EXPIRED",
                    "completed_at": now_utc,
                }
                if (
                    self._uses_task_budget(context)
                    and self._task_budget_service is not None
                ):
                    assert context.task_id is not None
                    timeout_revision = (
                        await self._task_budget_service.expire_task_scoped_waiting_execution(
                            context.task_id,
                            execution_id=context.execution_id,
                            source_revision=expected_revision,
                            transition_values=timeout_values,
                        )
                    )
                    if timeout_revision is None:
                        raise ExecutionConflictError(
                            "Stale AgentExecution revision/state while applying "
                            f"task-scoped timeout: {context.execution_id}@"
                            f"{expected_revision}"
                        )
                else:
                    await self._durable_store.compare_and_set_execution(
                        context.execution_id,
                        expected_revision,
                        timeout_values,
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

    async def cancel_activated_fork_execution(
        self,
        context: AgentExecutionContext,
        revision: int,
        *,
        error_message: str = "FORK_RUNTIME_HANDOFF_FAILED",
    ) -> None:
        """Fail closed after a durable FORK activation wins but local start fails."""

        context.freeze_active_budget()
        await self._cancel_durable_revision(
            context,
            revision,
            error_message=error_message,
        )

    async def cancel_activated_retry_execution(
        self,
        context: AgentExecutionContext,
        revision: int,
        *,
        error_message: str = "RETRY_RUNTIME_HANDOFF_FAILED",
    ) -> None:
        """Fail closed after retry activation wins but local handoff fails."""

        context.freeze_active_budget()
        await self._cancel_durable_revision(
            context,
            revision,
            error_message=error_message,
        )

    async def cancel_activated_aggregate_execution(
        self,
        context: AgentExecutionContext,
        revision: int,
        *,
        error_message: str = "AGGREGATE_RUNTIME_HANDOFF_FAILED",
    ) -> None:
        """Fail closed after aggregate activation wins but local handoff fails."""

        context.freeze_active_budget()
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
        *,
        recovery_fence: Mapping[str, Any] | None = None,
        expected_task_budget_incarnation_generation: int | None = None,
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
            recovery_fence=recovery_fence,
            expected_task_budget_incarnation_generation=(
                expected_task_budget_incarnation_generation
            ),
        )
        if target is AgentExecutionState.WAITING:
            context.waiting_checkpoint_transcript = []
            context.waiting_pending_invocations = []
            context.waiting_origin_connection_id = None
        return result

    @staticmethod
    def _recovery_fence_payload(
        activation: RecoveryActivationResult,
    ) -> dict[str, Any]:
        return {
            "owner_instance_id": activation.activation_owner_instance_id,
            "lease_generation": activation.lease_generation,
            "lease_expires_at": activation.lease_expires_at,
        }

    async def _require_recovery_owner_fence(
        self,
        context: AgentExecutionContext,
        activation: RecoveryActivationResult,
        *,
        provider_boundary: bool = False,
    ) -> None:
        checker = getattr(
            self._durable_store,
            "has_active_execution_lease_fence",
            None,
        )
        if not callable(checker):
            if provider_boundary:
                raise ProviderRecoveryAuthorityLostError(
                    "Durable R12 lease-fence predicate is unavailable.",
                    reason_code="RECOVERY_LEASE_FENCE_UNAVAILABLE",
                )
            raise ExecutionConflictError(
                "RECOVERY_LEASE_FENCE_UNAVAILABLE: "
                "durable R12 lease-fence predicate is unavailable."
            )
        active = await checker(
            context.execution_id,
            owner_instance_id=activation.activation_owner_instance_id,
            lease_generation=activation.lease_generation,
            now_utc=context.clock.now_utc(),
            expected_lease_expires_at=activation.lease_expires_at,
        )
        if active:
            return
        if provider_boundary:
            raise ProviderRecoveryAuthorityLostError(
                reason_code="RECOVERY_ACTIVE_LEASE_FENCE_LOST",
            )
        raise ExecutionConflictError(
            "RECOVERY_ACTIVE_LEASE_FENCE_LOST: "
            "exact R12 owner/generation/F2-expiry fence is stale."
        )

    async def _publish_recovery(
        self,
        event_name: str,
        context: AgentExecutionContext,
        activation: RecoveryActivationResult,
        **kwargs,
    ) -> None:
        await self._require_recovery_owner_fence(
            context,
            activation,
        )
        await self._publish(
            event_name,
            context,
            **kwargs,
        )

    async def execute_recovered_next_iteration(
        self,
        context: AgentExecutionContext,
        *,
        plan: RecoveryPlan,
        activation: RecoveryActivationResult,
        handoff_transcript: Sequence[InferenceMessage],
        recovered_tool_results: Sequence[ToolExecutionResult],
    ) -> AgentExecutionResult:
        """Run the single bounded F3-B fresh inference after durable handoff.

        This entrypoint is separate from ordinary execute(): recovery authority
        loss propagates to the R12 owner and is never converted into an
        ordinary Agent FAILED result.
        """
        if self._durable_store is None:
            raise ExecutionConflictError(
                "RECOVERY_DURABLE_STORE_REQUIRED: F3-B requires durable state."
            )
        if (
            plan.inference_disposition
            is not RecoveryInferenceDisposition.NO_INFERENCE
        ):
            raise RecoveryProgressionDeferredError(
                "RECOVERY_INFERENCE_CUT_UNPROVEN",
                "F3-B cannot replay or resume the frozen old inference.",
            )
        if (
            context.execution_id != plan.execution_id
            or context.iteration != plan.iteration
            or context.resume_revision != activation.consumed_execution_revision
            or activation.execution_id != plan.execution_id
            or activation.checkpoint_id != plan.checkpoint_id
        ):
            raise RecoveryProgressionDeferredError(
                "RECOVERY_CONTEXT_CONFLICT",
                "Prepared runtime context differs from F2/F3-A authority.",
            )
        expected_incarnation = plan.task_budget_incarnation_generation
        if context.task_id is not None and (
            isinstance(expected_incarnation, bool)
            or not isinstance(expected_incarnation, int)
            or expected_incarnation <= 0
        ):
            raise RecoveryProgressionDeferredError(
                "RECOVERY_TASK_BUDGET_AUTHORITY_UNPROVEN",
                "Task-scoped recovery lacks its frozen TaskBudget incarnation.",
            )
        if context.active_budget_running:
            raise RecoveryProgressionDeferredError(
                "RECOVERY_CONTEXT_CONFLICT",
                "F3-B requires the F3-A active budget frozen at handoff.",
            )

        recovery_fence = self._recovery_fence_payload(activation)
        transcript = list(handoff_transcript)
        latest_tool_results = tuple(recovered_tool_results)
        iterations: list[AgentIteration] = []
        total_usage = context.usage

        context.restore_active_budget()
        try:
            await self._require_recovery_owner_fence(context, activation)
            context.ensure_active()
            next_iteration = context.next_iteration()
            if next_iteration != plan.iteration + 1:
                raise RecoveryProgressionDeferredError(
                    "RECOVERY_ITERATION_CONFLICT",
                    "Fresh recovery iteration is not plan.iteration + 1.",
                )
            if context.begin_iteration_budget() <= 0.0:
                raise TimeoutError(
                    "Recovered Agent iteration deadline exceeded before start."
                )
            if (
                self._execution_policy.check_iteration(context, next_iteration)
                is not PolicyDecision.ALLOW
            ):
                raise RecoveryProgressionDeferredError(
                    "MAX_ITERATIONS_EXCEEDED",
                    "Recovered next iteration is not allowed by execution policy.",
                )

            record = AgentIteration(
                execution_id=context.execution_id,
                iteration=next_iteration,
                state=AgentLoopState.PREPARING,
            )
            iterations.append(record)
            await self._publish_recovery(
                AgentEventName.ITERATION_STARTED,
                context,
                activation,
                iteration=next_iteration,
            )
            await self._persist_iteration(
                record,
                recovery_fence=recovery_fence,
            )
            record.state = transition(
                record.state,
                AgentLoopState.THINKING,
            )

            snapshot = await self._await_contextual(
                self._context_builder.build(
                    context,
                    AgentContextRequest(
                        execution_id=context.execution_id,
                        iteration=next_iteration,
                        prior_messages=[
                            message.model_dump(mode="json")
                            for message in transcript
                        ],
                        history_mode=AgentContextHistoryMode.EXPLICIT,
                        tool_results=[],
                    ),
                ),
                context=context,
                timeout_seconds=context.remaining_iteration_seconds,
            )
            await self._require_recovery_owner_fence(context, activation)

            request_id = f"inf_{uuid.uuid4().hex}"
            record.inference_request_id = request_id
            await self._persist_iteration(
                record,
                recovery_fence=recovery_fence,
            )
            inference_timeout = context.remaining_for_operation(
                getattr(
                    context.limits,
                    "inference_timeout_seconds",
                    None,
                )
            )
            if inference_timeout <= 0:
                raise TimeoutError(
                    "Recovered Agent deadline exceeded before inference."
                )
            inference_deadline_monotonic = monotonic() + inference_timeout

            await self._publish_recovery(
                AgentEventName.INFERENCE_REQUESTED,
                context,
                activation,
                iteration=next_iteration,
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
                    recovery_execution_id=context.execution_id,
                    recovery_fence=recovery_fence,
                    expected_incarnation_generation=expected_incarnation,
                )

            async def recovery_pre_attempt_guard() -> None:
                await self._require_recovery_owner_fence(
                    context,
                    activation,
                    provider_boundary=True,
                )

            request = InferenceRequest(
                request_id=request_id,
                execution_id=context.execution_id,
                iteration=next_iteration,
                messages=list(snapshot.messages),
                tools=list(snapshot.tools),
                model=(
                    getattr(context.agent, "model", None)
                    or context.metadata.get("model")
                ),
                max_output_tokens=context.metadata.get("max_output_tokens"),
                timeout_seconds=inference_timeout,
                deadline_monotonic=inference_deadline_monotonic,
                owner_user_id=(
                    str(context.identity.user_id)
                    if context.identity.user_id
                    else None
                ),
                budget_identity=context.identity,
                cancellation_event=context.cancellation_event,
                recovery_pre_attempt_guard=recovery_pre_attempt_guard,
                metadata={
                    **dict(snapshot.metadata),
                    "quota_source_surface": "AGENT",
                    "session_id": context.session_id,
                    "task_id": context.task_id,
                    "workflow_id": context.workflow_id,
                    "agent_iteration_id": (
                        f"{context.execution_id}:iteration:{next_iteration}"
                    ),
                },
            )
            response = await self._inference.complete(request)

            if self._uses_task_budget(context):
                assert context.task_id is not None
                await self._task_budget_service.account_usage(
                    context.task_id,
                    usage_key=request_id,
                    tokens=response.usage.total_tokens,
                    cost_usd=response.usage.estimated_cost_usd,
                    recovery_execution_id=context.execution_id,
                    recovery_fence=recovery_fence,
                    expected_incarnation_generation=expected_incarnation,
                )

            await self._publish_recovery(
                AgentEventName.INFERENCE_COMPLETED,
                context,
                activation,
                iteration=next_iteration,
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
                inference_request=request,
                inference_response=response,
                recovery_fence=recovery_fence,
            )

            if response.message.tool_calls:
                raise RecoveryProgressionDeferredError(
                    "RECOVERY_FRESH_TOOL_DISPATCH_DEFERRED",
                    "Fresh recovered tool calls require a later stage.",
                )

            record.close(AgentLoopState.FINALIZING)
            record.close(AgentLoopState.COMPLETED)
            await self._persist_iteration(
                record,
                recovery_fence=recovery_fence,
            )
            await self._publish_recovery(
                AgentEventName.ITERATION_COMPLETED,
                context,
                activation,
                iteration=next_iteration,
                payload={"state": record.state.value},
            )
            result = AgentExecutionResult(
                execution_id=context.execution_id,
                agent_id=context.agent_id,
                state=AgentLoopState.COMPLETED,
                output=_extract_text(response.message.content),
                final_message=response.message,
                iterations=tuple(iterations),
                last_tool_results=latest_tool_results,
                usage=context.usage,
            )
            await self._publish_recovery(
                AgentEventName.EXECUTION_COMPLETED,
                context,
                activation,
                payload={"state": AgentLoopState.COMPLETED.value},
            )
            return await self._finish_durable_execution(
                context,
                result,
                activation.consumed_execution_revision,
                recovery_fence=recovery_fence,
                expected_task_budget_incarnation_generation=(
                    expected_incarnation
                ),
            )
        finally:
            context.freeze_active_budget()

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
            start_error_code = (
                "AGENT_TASK_TIMEOUT"
                if context.task_timed_out
                else "AGENT_EXECUTION_TIMEOUT"
                if context.timed_out
                else "AGENT_EXECUTION_NOT_ALLOWED"
            )
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
                payload={"error_code": start_error_code},
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
                error_code=start_error_code,
                error_message=(
                    "Task wall-clock time limit exceeded."
                    if context.task_timed_out
                    else "Agent execution was rejected by execution policy."
                ),
            )

        for iteration_number in range(
            context.iteration + 1,
            context.limits.max_iterations + 1,
        ):
            timeout_operation = "execution"
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

                timeout_operation = "context"
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

                # Freeze the already-clamped Agent/iteration/inference
                # remaining time into the process monotonic clock domain
                # before any awaited publication/accounting work.  The same
                # absolute deadline is then shared by the adapter and provider.
                inference_deadline_monotonic = (
                    monotonic() + inference_timeout
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

                timeout_operation = "inference"
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
                        max_output_tokens=context.metadata.get("max_output_tokens"),
                        timeout_seconds=inference_timeout,
                        deadline_monotonic=inference_deadline_monotonic,
                        owner_user_id=(
                            str(context.identity.user_id)
                            if context.identity.user_id
                            else None
                        ),
                        budget_identity=context.identity,
                        cancellation_event=context.cancellation_event,
                        metadata={
                            **dict(snapshot.metadata),
                            "quota_source_surface": "AGENT",
                            "session_id": context.session_id,
                            "task_id": context.task_id,
                            "workflow_id": context.workflow_id,
                            "agent_iteration_id": (
                                f"{context.execution_id}:iteration:{iteration_number}"
                            ),
                        },
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
                        max_output_tokens=context.metadata.get("max_output_tokens"),
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
                tool_activities: dict[str, dict[str, Any]] = {}
                assistant_text = _extract_text(response.message.content).strip()
                for request in tool_requests:
                    tool_definition = next(
                        (
                            item for item in snapshot.tools
                            if (
                                item.get("name") if isinstance(item, Mapping)
                                else getattr(item, "name", None)
                            ) == request.capability_id
                        ),
                        None,
                    )
                    description = (
                        tool_definition.get("description")
                        if isinstance(tool_definition, Mapping)
                        else getattr(tool_definition, "description", None)
                    )
                    purpose = (
                        f"Load instructions for skill {request.arguments.get('skill_id', '')}"
                        if request.capability_id == "skill.load"
                        else (assistant_text or description or f"Use {request.capability_id}")
                    )
                    activity = {
                        "capability_id": request.capability_id,
                        "purpose": str(purpose).strip()[:500] or f"Use {request.capability_id}",
                        "arguments": _public_tool_arguments(request.arguments),
                    }
                    tool_activities[request.tool_call_id] = activity
                await self._publish(
                    AgentEventName.PROGRESS,
                    context,
                    iteration=iteration_number,
                    payload={
                        "content": assistant_text,
                        "tool_calls": [
                            {
                                "tool_call_id": request.tool_call_id,
                                "name": request.capability_id,
                                "purpose": tool_activities[request.tool_call_id]["purpose"],
                                "arguments": tool_activities[request.tool_call_id]["arguments"],
                            }
                            for request in tool_requests
                        ],
                    },
                )
                for request in tool_requests:
                    activity = tool_activities[request.tool_call_id]
                    await self._publish(
                        AgentEventName.TOOL_REQUESTED,
                        context,
                        iteration=iteration_number,
                        tool_call_id=request.tool_call_id,
                        invocation_id=request.invocation_id,
                        payload=activity,
                    )
                    await self._publish(
                        AgentEventName.TOOL_STARTED,
                        context,
                        iteration=iteration_number,
                        tool_call_id=request.tool_call_id,
                        invocation_id=request.invocation_id,
                        payload=activity,
                    )
                    await self._persist_tool_call(request, iteration_id)
                await self._reserve_task_tool_calls(
                    context,
                    tool_requests,
                )
                try:
                    timeout_operation = "tool"
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
                                **tool_activities[request.tool_call_id],
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
                                **tool_activities[request.tool_call_id],
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
                    if (
                        self._durable_store is None
                        or request.capability_id == "agent.budget.configure"
                    ):
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
                            **tool_activities.get(result.tool_call_id, {"capability_id": result.capability_id}),
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
                if context.task_id is None and context.metadata.get("agent_time_budget_enabled"):
                    timed_out_tools = [
                        item.capability_id
                        for item in latest_tool_results
                        if item.error_code in {"CAPABILITY_TIMEOUT", "TERMINAL_TIMEOUT"}
                    ]
                    if timed_out_tools:
                        transcript.append(InferenceMessage(
                            role="system",
                            content=(
                                "A tool call timed out: "
                                + ", ".join(timed_out_tools)
                                + ". Do not assume that the operation had no side effects. "
                                "Use agent.budget.configure to reallocate operation time "
                                "within the task deadline before deciding whether to retry. "
                                f"Task time remaining: {context.remaining_seconds:.1f} seconds."
                            ),
                        ))
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
            except (
                asyncio.TimeoutError,
                TimeoutError,
                ProviderDeadlineExceededError,
            ) as timeout_error:
                if isinstance(
                    timeout_error,
                    (ProviderTimeoutError, ProviderDeadlineExceededError),
                ):
                    timeout_code = timeout_error.code
                    timeout_message = str(timeout_error)
                elif context.task_timed_out:
                    timeout_code = "AGENT_TASK_TIMEOUT"
                    timeout_message = "Task wall-clock time limit exceeded."
                elif context.timed_out:
                    timeout_code = "AGENT_EXECUTION_TIMEOUT"
                    timeout_message = "Agent execution time budget exceeded."
                elif context.iteration_timed_out:
                    timeout_code = "AGENT_ITERATION_TIMEOUT"
                    timeout_message = "Agent iteration time budget exceeded."
                else:
                    timeout_code = {
                        "context": "AGENT_ITERATION_TIMEOUT",
                        "inference": "AGENT_INFERENCE_TIMEOUT",
                        "tool": "AGENT_TOOL_TIMEOUT",
                    }.get(timeout_operation, "AGENT_EXECUTION_TIMEOUT")
                    timeout_scope = "iteration" if timeout_operation == "context" else timeout_operation
                    timeout_message = f"Agent {timeout_scope} time budget exceeded."
                if (
                    timeout_code == "AGENT_INFERENCE_TIMEOUT"
                    and context.task_id is None
                    and context.metadata.get("agent_time_budget_enabled")
                    and iteration_number < context.limits.max_iterations
                    and context.remaining_seconds > 1.0
                ):
                    next_timeout = min(
                        float(context.metadata["task_max_timeout_seconds"]),
                        context.limits.inference_timeout_seconds * 2,
                    )
                    context.limits.inference_timeout_seconds = next_timeout
                    context.limits.iteration_timeout_seconds = max(
                        context.limits.iteration_timeout_seconds,
                        next_timeout + 5,
                    )
                    record.close(
                        AgentLoopState.TIMEOUT,
                        error_code=timeout_code,
                    )
                    await self._persist_iteration(record)
                    await self._publish(
                        AgentEventName.ITERATION_COMPLETED,
                        context,
                        iteration=record.iteration,
                        payload={"state": record.state.value, "error_code": timeout_code},
                    )
                    transcript.append(InferenceMessage(
                        role="system",
                        content=(
                            "The previous model call timed out. Its limit was increased "
                            f"to {next_timeout:g} seconds for this retry. "
                            "If this task needs more time, call agent.budget.configure "
                            "to propose a task deadline and allocate operation time."
                        ),
                    ))
                    await self._publish(
                        AgentEventName.PROGRESS,
                        context,
                        iteration=iteration_number,
                        payload={
                            "content": "Model call timed out; retrying within the task time budget.",
                            "error_code": timeout_code,
                        },
                    )
                    await self._persist_execution_checkpoint(context, transcript)
                    context.clear_iteration_budget()
                    continue
                record = iterations[-1] if iterations else None
                if record is not None and record.state not in {
                    AgentLoopState.COMPLETED,
                    AgentLoopState.CANCELLED,
                    AgentLoopState.TIMEOUT,
                }:
                    record.close(
                        AgentLoopState.TIMEOUT,
                        error_code=timeout_code,
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
                        payload={"error_code": timeout_code},
                    )
                return self._terminal_result(
                    context,
                    iterations,
                    context.usage,
                    AgentLoopState.TIMEOUT,
                    timeout_code,
                    timeout_message,
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


def _public_tool_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Bound and redact tool arguments before publishing UI activity."""
    sensitive = ("password", "secret", "token", "credential", "authorization", "api_key", "base64", "content", "data")

    def clean(value: Any, *, depth: int = 0) -> Any:
        if depth >= 3:
            return "[nested value]"
        if isinstance(value, Mapping):
            return {
                str(key): (
                    "[redacted]"
                    if any(mark in str(key).lower() for mark in sensitive)
                    else clean(item, depth=depth + 1)
                )
                for key, item in list(value.items())[:20]
            }
        if isinstance(value, (list, tuple)):
            return [clean(item, depth=depth + 1) for item in value[:20]]
        if isinstance(value, str):
            return value[:300] + ("…" if len(value) > 300 else "")
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return "[value]"

    return clean(arguments)


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
