from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Mapping, Sequence

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
    ) -> None:
        self._context_builder = context_builder
        self._inference = inference
        self._tool_execution = tool_execution
        self._execution_policy = execution_policy

    async def execute(self, context: AgentExecutionContext) -> AgentExecutionResult:
        """Execute one agent until a final answer or terminal failure."""
        iterations: list[AgentIteration] = []
        transcript: list[InferenceMessage] = []
        latest_tool_results: tuple[ToolExecutionResult, ...] = ()
        total_usage = context.usage

        if self._execution_policy.check_start(context) is not PolicyDecision.ALLOW:
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

        for iteration_number in range(1, context.limits.max_iterations + 1):
            try:
                context.ensure_active()
                context.next_iteration()

                if (
                    self._execution_policy.check_iteration(context, iteration_number)
                    is not PolicyDecision.ALLOW
                ):
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
                    timeout_seconds=context.remaining_seconds,
                )

                # The first snapshot contains the authoritative session/system
                # history. Seed the canonical transcript exactly once.
                if not transcript:
                    transcript.extend(snapshot.messages)

                request_id = f"inf_{uuid.uuid4().hex}"
                record.inference_request_id = request_id
                inference_timeout = context.remaining_for(
                    getattr(context.limits, "inference_timeout_seconds", None)
                )
                if inference_timeout <= 0:
                    raise TimeoutError(
                        "Agent execution deadline exceeded before inference."
                    )

                response = await self._inference.complete(
                    InferenceRequest(
                        request_id=request_id,
                        execution_id=context.execution_id,
                        iteration=iteration_number,
                        messages=list(snapshot.messages),
                        tools=list(snapshot.tools),
                        model=getattr(context.agent, "model", None),
                        timeout_seconds=inference_timeout,
                        cancellation_event=context.cancellation_event,
                        metadata=dict(snapshot.metadata),
                    )
                )

                transcript.append(response.message)
                total_usage = _add_usage(total_usage, response.usage)
                context.usage = total_usage

                if not response.message.tool_calls:
                    record.close(AgentLoopState.FINALIZING)
                    record.close(AgentLoopState.COMPLETED)
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
                        arguments=dict(tool_call.arguments),
                    )
                    for tool_call in response.message.tool_calls
                ]
                record.tool_call_ids = [item.tool_call_id for item in tool_requests]

                record.state = transition(record.state, AgentLoopState.WAITING_TOOL)
                raw_tool_results = await self._await_contextual(
                    self._tool_execution.execute_many(
                        context,
                        tool_requests,
                        max_parallel=context.limits.max_parallel_tools,
                    ),
                    context=context,
                    timeout_seconds=context.remaining_seconds,
                )
                latest_tool_results = tuple(
                    _order_tool_results(tool_requests, raw_tool_results)
                )

                # ToolExecutionAdapter may update context.usage with per-tool
                # accounting. Context is authoritative after the tool batch.
                total_usage = context.usage
                record.close(AgentLoopState.THINKING)

                transcript.extend(_tool_results_to_messages(latest_tool_results))

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
                record = iterations[-1] if iterations else None
                if record is not None and record.state not in {
                    AgentLoopState.COMPLETED,
                    AgentLoopState.CANCELLED,
                    AgentLoopState.TIMEOUT,
                }:
                    record.close(
                        AgentLoopState.FAILED,
                        error_code=getattr(exc, "code", type(exc).__name__),
                    )
                return self._terminal_result(
                    context,
                    iterations,
                    context.usage,
                    AgentLoopState.FAILED,
                    getattr(exc, "code", type(exc).__name__),
                    str(exc),
                    last_tool_results=latest_tool_results,
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