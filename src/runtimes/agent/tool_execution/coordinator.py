from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Sequence

from ..contracts.context import AgentExecutionContext
from ..contracts.tool import (
    ToolExecutionPort,
    ToolExecutionRequest,
    ToolExecutionResult,
)


RetryDecider = Callable[[ToolExecutionResult, int], bool]


class AgentToolExecutionCoordinator(ToolExecutionPort):
    """Canonical orchestration boundary for agent tool execution.

    The coordinator owns batch-level invariants: request validation, duplicate
    detection, bounded scheduling, deterministic result ordering, and optional
    retry policy. The concrete capability adapter remains responsible for
    policy/auth/schema validation and CapabilityRuntime invocation.
    """

    def __init__(
        self,
        executor: ToolExecutionPort,
        *,
        retry_decider: RetryDecider | None = None,
        max_attempts: int = 1,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1.")
        self._executor = executor
        self._retry_decider = retry_decider or self._never_retry
        self._max_attempts = max_attempts

    async def execute(
        self,
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> ToolExecutionResult:
        self._validate_request(context, request)

        attempt = 0
        while True:
            attempt += 1
            result = await self._executor.execute(context, request)
            if (
                attempt >= self._max_attempts
                or not result.retryable
                or not self._retry_decider(result, attempt)
            ):
                return self._with_attempt(result, attempt)

            context.ensure_active()

    async def execute_many(
        self,
        context: AgentExecutionContext,
        requests: Sequence[ToolExecutionRequest],
        *,
        max_parallel: int,
    ) -> Sequence[ToolExecutionResult]:
        if max_parallel < 1:
            raise ValueError("max_parallel must be >= 1.")

        request_list = list(requests)
        self._validate_batch(context, request_list)

        if not request_list:
            return []

        semaphore = asyncio.Semaphore(
            min(max_parallel, context.limits.max_parallel_tools)
        )

        async def run_one(request: ToolExecutionRequest) -> ToolExecutionResult:
            async with semaphore:
                return await self.execute(context, request)

        raw_results = await asyncio.gather(
            *(run_one(request) for request in request_list)
        )
        return self._order_results(request_list, raw_results)

    @staticmethod
    def _validate_request(
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> None:
        if request.execution_id != context.execution_id:
            raise ValueError(
                "Tool request execution_id does not match execution context."
            )
        if request.iteration < 1:
            raise ValueError("Tool request iteration must be >= 1.")
        if not request.tool_call_id:
            raise ValueError("Tool request tool_call_id must be non-empty.")
        if not request.invocation_id:
            raise ValueError("Tool request invocation_id must be non-empty.")
        if not request.capability_id:
            raise ValueError("Tool request capability_id must be non-empty.")

    @classmethod
    def _validate_batch(
        cls,
        context: AgentExecutionContext,
        requests: Sequence[ToolExecutionRequest],
    ) -> None:
        seen: set[str] = set()
        for request in requests:
            cls._validate_request(context, request)
            if request.tool_call_id in seen:
                raise ValueError(
                    "Duplicate tool_call_id in one execution batch: "
                    f"{request.tool_call_id!r}."
                )
            seen.add(request.tool_call_id)

    @staticmethod
    def _order_results(
        requests: Sequence[ToolExecutionRequest],
        results: Sequence[ToolExecutionResult],
    ) -> list[ToolExecutionResult]:
        by_id: dict[str, ToolExecutionResult] = {}
        for result in results:
            if result.tool_call_id in by_id:
                raise ValueError(
                    "Duplicate tool result for tool_call_id="
                    f"{result.tool_call_id!r}."
                )
            by_id[result.tool_call_id] = result

        ordered: list[ToolExecutionResult] = []
        for request in requests:
            result = by_id.get(request.tool_call_id)
            if result is None:
                raise ValueError(
                    "Missing tool result for tool_call_id="
                    f"{request.tool_call_id!r}."
                )
            if result.execution_id != request.execution_id:
                raise ValueError(
                    "Tool result execution_id does not match request."
                )
            if result.iteration != request.iteration:
                raise ValueError(
                    "Tool result iteration does not match request."
                )
            if result.capability_id != request.capability_id:
                raise ValueError(
                    "Tool result capability_id does not match request."
                )
            ordered.append(result)

        if len(ordered) != len(results):
            raise ValueError("Tool execution returned an unexpected result count.")
        return ordered

    @staticmethod
    def _with_attempt(
        result: ToolExecutionResult,
        attempt: int,
    ) -> ToolExecutionResult:
        if result.metadata.get("attempt") == attempt:
            return result
        return result.model_copy(
            update={
                "metadata": {
                    **result.metadata,
                    "attempt": attempt,
                }
            }
        )

    @staticmethod
    def _never_retry(result: ToolExecutionResult, attempt: int) -> bool:
        return False
