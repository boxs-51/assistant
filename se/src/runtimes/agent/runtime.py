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
from .contracts.policy import AgentExecutionPolicy, PolicyDecision
from .contracts.events import (
    AgentEventEnvelope,
    AgentEventName,
    AgentEventPublisher,
    CorrelationContext,
)
from .contracts.continuation import ContinuationState
from .persistence import ExecutionConflictError
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
        continuation_service=None,
        wait_policy: ExecutionWaitPolicy | None = None,
    ) -> None:
        self._context_builder = context_builder
        self._inference = inference
        self._tool_execution = tool_execution
        self._execution_policy = execution_policy
        self._durable_store = durable_store
        self._event_publisher = event_publisher
        self._continuation_service = continuation_service
        self._wait_policy = (
            wait_policy or ConfiguredExecutionWaitPolicy()
        )

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
        record = await self._durable_store.load_tool_result(
            request.execution_id,
            request.tool_call_id,
        )
        if record is None:
            return None
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
            for result in executed:
                await self._persist_tool_result(result, iteration_id)
        by_id = {item.tool_call_id: item for item in [*committed, *executed]}
        return tuple(by_id[item.tool_call_id] for item in requests)

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
            try:
                await self._durable_store.save_execution(
                    {
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
                )
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
        await self._durable_store.compare_and_set_execution(
            context.execution_id,
            expected_revision,
            {
                "state": AgentExecutionState.RUNNING.value,
                "wait_reason": None,
                "wait_expires_at": None,
                "started_at": context.clock.now_utc(),
            },
        )

        if current_state is AgentExecutionState.WAITING:
            assert resume_remaining is not None
            context.remaining_active_budget_seconds = resume_remaining
            context.wait_expires_at = None
            context.restore_active_budget(resume_remaining)

        return expected_revision + 1

    async def claim_resume(self, context: AgentExecutionContext) -> int:
        """Synchronously claim one durable WAITING execution before WS ACK."""
        revision = await self._begin_durable_execution(context)
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
                        "continuation_state": None,
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

        await self._durable_store.compare_and_set_execution(
            result.execution_id,
            expected_revision,
            values,
        )
        return result

    async def execute(
        self,
        context: AgentExecutionContext,
        *,
        durable_revision: int | None = None,
    ) -> AgentExecutionResult:
        """Run exactly one durable AgentExecution lifecycle."""
        revision = (
            durable_revision
            if durable_revision is not None
            else await self._begin_durable_execution(context)
        )
        try:
            result = await self._execute_loop(context)
        except asyncio.CancelledError:
            if revision is not None:
                await self._durable_store.compare_and_set_execution(
                    context.execution_id,
                    revision,
                    {
                        "state": AgentExecutionState.CANCELLED.value,
                        "wait_reason": None,
                        "error": "Agent execution cancelled.",
                        "completed_at": datetime.now(timezone.utc),
                    },
                )
            raise
        except Exception as exc:
            if revision is not None:
                await self._durable_store.compare_and_set_execution(
                    context.execution_id,
                    revision,
                    {
                        "state": AgentExecutionState.FAILED.value,
                        "wait_reason": None,
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

    async def _execute_loop(self, context: AgentExecutionContext) -> AgentExecutionResult:
        """Execute one agent until a final answer or terminal failure."""
        # The runtime should persist each iteration and tool checkpoint before
        # continuing the loop, then resume from the last durable checkpoint.
        iterations: list[AgentIteration] = []
        transcript: list[InferenceMessage] = [
            InferenceMessage.model_validate(item)
            for item in context.resume_transcript
        ]
        latest_tool_results: tuple[ToolExecutionResult, ...] = ()
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
                if not transcript:
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
                for result in latest_tool_results:
                    await self._persist_tool_result(result, iteration_id)
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

                disconnected = [
                    item
                    for item in latest_tool_results
                    if item.metadata.get("original_error_code")
                    == "REMOTE_CONNECTION_LOST"
                ]
                if disconnected and self._continuation_service is not None:
                    lost = disconnected[0]
                    server_continuation_available = bool(
                        getattr(
                            self._tool_execution,
                            "can_continue_server_side",
                            lambda capability_id: False,
                        )(lost.capability_id)
                    )
                    old_connection_id = (
                        lost.metadata.get("connection_id")
                        or context.connection_id
                        or ""
                    )
                    checkpoint_transcript = [
                        item.model_dump(mode="json")
                        for item in [
                            *transcript,
                            *_tool_results_to_messages(latest_tool_results),
                        ]
                    ]
                    checkpoint = await self._continuation_service.checkpoint_disconnect(
                        execution_id=context.execution_id,
                        session_id=context.session_id,
                        owner_user_id=context.identity.user_id,
                        connection_id=old_connection_id,
                        invocation_id=(
                            lost.metadata.get("invocation_id")
                            or lost.invocation_id
                        ),
                        tool_call_id=lost.tool_call_id,
                        capability_id=lost.capability_id,
                        iteration=iteration_number,
                        transcript=checkpoint_transcript,
                        server_continuation_available=server_continuation_available,
                        metadata={
                            "origin_client_id": context.metadata.get("client_id")
                        },
                    )
                    context.connection_id = None
                    if checkpoint.state is ContinuationState.WAITING:
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
                            continuation_state=checkpoint.state,
                            checkpoint_id=checkpoint.checkpoint_id,
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