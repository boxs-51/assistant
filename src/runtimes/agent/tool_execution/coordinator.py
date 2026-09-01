from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Sequence

from ..contracts.context import AgentExecutionContext
from ..contracts.tool import (
    ToolExecutionPort,
    ToolExecutionRequest,
    ToolExecutionResult,
)


RetryDecider = Callable[[ToolExecutionResult, int], bool]


@dataclass
class _ExecutionEntry:
    task: asyncio.Task[ToolExecutionResult]
    fingerprint: str
    waiters: int = 0
    cancel_requested: bool = False


@dataclass(frozen=True)
class _CompletedEntry:
    result: ToolExecutionResult
    fingerprint: str


class AgentToolExecutionCoordinator(ToolExecutionPort):
    """Canonical orchestration boundary for agent tool execution.

    The coordinator owns batch-level invariants: request validation, duplicate
    detection, bounded scheduling, deterministic result ordering, and optional
    retry policy.

    Duplicate execution protection is scoped to the lifetime of this
    coordinator and keyed by execution_id + invocation_id. This prevents
    concurrent duplicate dispatches without introducing persistence into
    Phase 5.6.
    """

    def __init__(
        self,
        executor: ToolExecutionPort,
        *,
        retry_decider: RetryDecider | None = None,
        max_attempts: int | None = None,
    ) -> None:
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be >= 1 when provided.")
        self._executor = executor
        self._retry_decider = retry_decider or self._never_retry
        self._max_attempts = max_attempts
        self._inflight: dict[tuple[str, str], _ExecutionEntry] = {}
        self._completed: dict[
            tuple[str, str],
            _CompletedEntry,
        ] = {}
        self._ledger_lock = asyncio.Lock()

    async def execute(
        self,
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> ToolExecutionResult:
        self._validate_request(context, request)

        key = (request.execution_id, request.invocation_id)
        fingerprint = self._request_fingerprint(request)

        while True:
            cleanup_task: asyncio.Task[ToolExecutionResult] | None = None

            async with self._ledger_lock:
                completed = self._completed.get(key)
                if completed is not None:
                    if completed.fingerprint != fingerprint:
                        raise ValueError(
                            "Conflicting request for existing invocation_id."
                        )
                    return completed.result

                existing = self._inflight.get(key)
                if existing is not None:
                    if existing.fingerprint != fingerprint:
                        raise ValueError(
                            "Conflicting request for in-flight invocation_id."
                        )

                    if existing.cancel_requested:
                        cleanup_task = existing.task
                    else:
                        existing.waiters += 1
                        task = existing.task
                else:
                    task = asyncio.create_task(
                        self._execute_once_or_retry(context, request),
                        name=(
                            f"tool:{request.execution_id}:"
                            f"{request.invocation_id}"
                        ),
                    )
                    self._inflight[key] = _ExecutionEntry(
                        task=task,
                        fingerprint=fingerprint,
                        waiters=1,
                    )

            if cleanup_task is None:
                break

            await asyncio.gather(
                cleanup_task,
                return_exceptions=True,
            )

            async with self._ledger_lock:
                current = self._inflight.get(key)
                if current is not None and current.task is cleanup_task:
                    self._inflight.pop(key, None)

        try:
            result = await self._await_with_cancellation(
                context,
                task,
            )
        except BaseException:
            cancel_task = False
            async with self._ledger_lock:
                current = self._inflight.get(key)
                if current is not None and current.task is task:
                    current.waiters -= 1
                    if current.waiters <= 0:
                        current.cancel_requested = True
                        if not task.done():
                            task.cancel()
                            cancel_task = True
                        else:
                            self._inflight.pop(key, None)

            if cancel_task:
                await asyncio.gather(
                    task,
                    return_exceptions=True,
                )
            raise

        async with self._ledger_lock:
            current = self._inflight.get(key)
            if current is not None and current.task is task:
                current.waiters -= 1
                if task.done():
                    self._inflight.pop(key, None)
                    self._completed[key] = _CompletedEntry(
                        result=result,
                        fingerprint=fingerprint,
                    )

        return result

    @staticmethod
    def _request_fingerprint(
        request: ToolExecutionRequest,
    ) -> str:
        """Return a stable semantic fingerprint for idempotency validation."""
        payload = {
            "execution_id": request.execution_id,
            "iteration": request.iteration,
            "invocation_id": request.invocation_id,
            "tool_call_id": request.tool_call_id,
            "capability_id": request.capability_id,
            "arguments": request.arguments,
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    async def _execute_once_or_retry(
        self,
        context: AgentExecutionContext,
        request: ToolExecutionRequest,
    ) -> ToolExecutionResult:
        attempt = 1
        max_retry_attempts = context.limits.max_retry_attempts
        if max_retry_attempts < 0:
            raise ValueError("max_retry_attempts must be >= 0.")

        execution_max_attempts = 1 + max_retry_attempts
        if self._max_attempts is not None:
            execution_max_attempts = min(
                execution_max_attempts,
                self._max_attempts,
            )

        while True:
            context.ensure_active()

            result = await self._executor.execute(
                context,
                request,
            )

            if not result.retryable:
                return self._with_attempt(result, attempt)

            if attempt >= execution_max_attempts:
                return self._with_attempt(result, attempt)

            if not self._retry_decider(result, attempt):
                return self._with_attempt(result, attempt)

            context.ensure_active()

            if not await context.reserve_retry_attempt():
                return self._with_attempt(result, attempt)

            attempt += 1

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

        tasks = [
            asyncio.create_task(
                run_one(request),
                name=(
                    f"tool-batch:{context.execution_id}:"
                    f"{request.invocation_id}"
                ),
            )
            for request in request_list
        ]

        try:
            raw_results = await self._gather_with_cancellation(
                context,
                tasks,
            )
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        return self._order_results(request_list, raw_results)

    async def _await_with_cancellation(
        self,
        context: AgentExecutionContext,
        task: asyncio.Task[ToolExecutionResult],
    ) -> ToolExecutionResult:
        if task.done():
            return await task

        context.ensure_active()

        cancellation_task = asyncio.create_task(
            context.cancellation_event.wait(),
            name=f"tool-cancel:{context.execution_id}",
        )

        try:
            done, _ = await asyncio.wait(
                {task, cancellation_task},
                timeout=context.remaining_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if task in done:
                return await task

            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

            if cancellation_task in done:
                raise asyncio.CancelledError

            raise TimeoutError(
                "Agent execution deadline exceeded during tool execution."
            )
        finally:
            if not cancellation_task.done():
                cancellation_task.cancel()
            await asyncio.gather(
                cancellation_task,
                return_exceptions=True,
            )

    async def _gather_with_cancellation(
        self,
        context: AgentExecutionContext,
        tasks: Sequence[asyncio.Task[ToolExecutionResult]],
    ) -> list[ToolExecutionResult]:
        cancellation_task = asyncio.create_task(
            context.cancellation_event.wait(),
            name=f"tool-batch-cancel:{context.execution_id}",
        )

        gather_task = asyncio.create_task(
            self._gather_tasks(tasks),
            name=f"tool-batch-gather:{context.execution_id}",
        )

        try:
            done, _ = await asyncio.wait(
                {gather_task, cancellation_task},
                timeout=context.remaining_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if gather_task in done:
                return list(await gather_task)

            gather_task.cancel()
            await asyncio.gather(
                gather_task,
                return_exceptions=True,
            )

            if cancellation_task in done:
                raise asyncio.CancelledError

            raise TimeoutError(
                "Agent execution deadline exceeded during tool batch."
            )
        finally:
            if not cancellation_task.done():
                cancellation_task.cancel()
            await asyncio.gather(
                cancellation_task,
                return_exceptions=True,
            )
    async def _gather_tasks(
        self,
        tasks: Sequence[asyncio.Task[ToolExecutionResult]],
    ) -> list[ToolExecutionResult]:
        return list(await asyncio.gather(*tasks))

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