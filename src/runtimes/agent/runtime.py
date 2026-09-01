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
        pending_tool_results: Sequence[ToolExecutionResult] = ()
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
            except asyncio.CancelledError:
                return self._terminal_result(
                    context,
                    iterations,
                    total_usage,
                    AgentLoopState.CANCELLED,
                    "AGENT_CANCELLED",
                    "Agent execution cancelled.",
                )
            except TimeoutError:
                return self._terminal_result(
                    context,
                    iterations,
                    total_usage,
                    AgentLoopState.TIMEOUT,
                    "AGENT_TIMEOUT",
                    "Agent execution timed out.",
                )

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
                )

            record = AgentIteration(
                execution_id=context.execution_id,
                iteration=iteration_number,
                state=AgentLoopState.PREPARING,
            )
            iterations.append(record)

            try:
                record.state = transition(record.state, AgentLoopState.THINKING)

                snapshot = await self._context_builder.build(
                    context,
                    AgentContextRequest(
                        execution_id=context.execution_id,
                        iteration=iteration_number,
                        prior_messages=[message.model_dump(mode="json") for message in transcript],
                        tool_results=list(pending_tool_results),
                    ),
                )

                request_id = f"inf_{uuid.uuid4().hex}"
                record.inference_request_id = request_id
                inference_timeout = context.remaining_for(
                    getattr(context.limits, "inference_timeout_seconds", None)
                )
                if inference_timeout <= 0:
                    raise TimeoutError("Agent execution deadline exceeded before inference.")

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
                        usage=total_usage,
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
                pending_tool_results = await self._tool_execution.execute_many(
                    context,
                    tool_requests,
                    max_parallel=context.limits.max_parallel_tools,
                )
                record.close(AgentLoopState.THINKING)
                # Tool results are now part of the canonical transcript. Do not
                # also pass them through AgentContextRequest.tool_results on the
                # next iteration, otherwise ContextBuilderAdapter would append
                # the same result twice.
                transcript.extend(_tool_results_to_messages(pending_tool_results))
                pending_tool_results = ()

            except asyncio.CancelledError:
                record.close(AgentLoopState.CANCELLED, error_code="AGENT_CANCELLED")
                return self._terminal_result(
                    context,
                    iterations,
                    total_usage,
                    AgentLoopState.CANCELLED,
                    "AGENT_CANCELLED",
                    "Agent execution cancelled.",
                )
            except (asyncio.TimeoutError, TimeoutError):
                record.close(AgentLoopState.TIMEOUT, error_code="AGENT_TIMEOUT")
                return self._terminal_result(
                    context,
                    iterations,
                    total_usage,
                    AgentLoopState.TIMEOUT,
                    "AGENT_TIMEOUT",
                    "Agent execution timed out.",
                )
            except Exception as exc:
                record.close(
                    AgentLoopState.FAILED,
                    error_code=getattr(exc, "code", type(exc).__name__),
                )
                return self._terminal_result(
                    context,
                    iterations,
                    total_usage,
                    AgentLoopState.FAILED,
                    getattr(exc, "code", type(exc).__name__),
                    str(exc),
                    last_tool_results=pending_tool_results,
                )

        return self._terminal_result(
            context,
            iterations,
            total_usage,
            AgentLoopState.FAILED,
            "MAX_ITERATIONS_EXCEEDED",
            "Agent iteration limit exceeded.",
            last_tool_results=pending_tool_results,
        )

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