from __future__ import annotations

import asyncio

import pytest

from src.domain.schemas.agent import AgentDefinition
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity
from src.runtimes.agent.contracts import (
    AgentExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from src.runtimes.agent.tool_execution import AgentToolExecutionCoordinator


def make_context() -> AgentExecutionContext:
    agent = AgentDefinition(
        name="coordinator-test-agent",
        goal="test",
        instruction="test",
        tools=["tool.a", "tool.b", "tool.c"],
    )
    return AgentExecutionContext.create(
        execution_id="exec-coordinator-test",
        agent_id=agent.name,
        session_id="session-coordinator-test",
        correlation_id="corr-coordinator-test",
        identity=Identity(user_id="u1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=8,
            max_parallel_tools=2,
            timeout_seconds=5,
            tool_timeout_seconds=1,
        ),
        agent=agent,
    )


def request(context: AgentExecutionContext, call_id: str, capability: str) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id=f"inv-{call_id}",
        tool_call_id=call_id,
        capability_id=capability,
        arguments={},
    )


def result_for(req: ToolExecutionRequest, *, execution_id: str | None = None, iteration: int | None = None, capability_id: str | None = None) -> ToolExecutionResult:
    return ToolExecutionResult(
        execution_id=execution_id or req.execution_id,
        iteration=req.iteration if iteration is None else iteration,
        invocation_id=req.invocation_id,
        tool_call_id=req.tool_call_id,
        capability_id=capability_id or req.capability_id,
        success=True,
        output=req.tool_call_id,
    )


@pytest.mark.asyncio
async def test_coordinator_rejects_duplicate_tool_call_ids_before_dispatch():
    context = make_context()
    calls = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal calls
            calls += 1
            return result_for(request)

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())
    first = request(context, "call-1", "tool.a")
    duplicate = request(context, "call-1", "tool.b")

    with pytest.raises(ValueError, match="Duplicate tool_call_id"):
        await coordinator.execute_many(context, [first, duplicate], max_parallel=2)
    assert calls == 0


@pytest.mark.asyncio
async def test_coordinator_restores_original_tool_call_order():
    context = make_context()
    requests = [
        request(context, "call-a", "tool.a"),
        request(context, "call-b", "tool.b"),
        request(context, "call-c", "tool.c"),
    ]

    class Executor:
        async def execute(self, context, request):
            await asyncio.sleep({"call-a": 0.03, "call-b": 0.01, "call-c": 0.0}[request.tool_call_id])
            return result_for(request)

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())
    results = await coordinator.execute_many(context, requests, max_parallel=2)

    assert [item.tool_call_id for item in results] == ["call-a", "call-b", "call-c"]


@pytest.mark.asyncio
async def test_coordinator_bounds_parallel_dispatch():
    context = make_context()
    active = 0
    peak = 0
    lock = asyncio.Lock()

    class Executor:
        async def execute(self, context, request):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                return result_for(request)
            finally:
                async with lock:
                    active -= 1

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())
    requests = [request(context, f"call-{i}", f"tool-{i}") for i in range(6)]
    results = await coordinator.execute_many(context, requests, max_parallel=6)

    assert peak == 2
    assert len(results) == 6


@pytest.mark.asyncio
async def test_coordinator_rejects_mismatched_downstream_result():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    class Executor:
        async def execute(self, context, request):
            return result_for(request, execution_id="wrong-execution")

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())

    with pytest.raises(ValueError, match="execution_id"):
        await coordinator.execute_many(context, [req], max_parallel=1)


@pytest.mark.asyncio
async def test_coordinator_rejects_mismatched_result_iteration():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    class Executor:
        async def execute(self, context, request):
            return result_for(request, iteration=2)

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())

    with pytest.raises(ValueError, match="iteration"):
        await coordinator.execute_many(context, [req], max_parallel=1)


@pytest.mark.asyncio
async def test_coordinator_rejects_mismatched_capability():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    class Executor:
        async def execute(self, context, request):
            return result_for(request, capability_id="tool.b")

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("coordinator must own batch orchestration")

    coordinator = AgentToolExecutionCoordinator(Executor())

    with pytest.raises(ValueError, match="capability_id"):
        await coordinator.execute_many(context, [req], max_parallel=1)


@pytest.mark.asyncio
async def test_coordinator_rejects_duplicate_downstream_results():
    context = make_context()
    req = request(context, "call-1", "tool.a")
    base = result_for(req)
    duplicate = base.model_copy(
        update={"invocation_id": "inv-duplicate"}
    )
    with pytest.raises(ValueError, match="Duplicate tool result"):
        AgentToolExecutionCoordinator._order_results(
            [req],
            [base, duplicate],
        )


@pytest.mark.asyncio
async def test_coordinator_rejects_missing_downstream_result():
    context = make_context()
    req = request(context, "call-1", "tool.a")
    with pytest.raises(ValueError, match="Missing tool result"):
        AgentToolExecutionCoordinator._order_results(
            [req],
            [],
        )


@pytest.mark.asyncio
async def test_coordinator_capped_by_max_attempts():
    """Kiểm tra giới hạn bị siết bởi max_attempts của Coordinator."""
    context = make_context()
    context.limits.max_retry_attempts = 5  # Context cho phép tổng 1 + 5 = 6 lần chạy
    attempts = 0
    req = request(context, "call-1", "tool.a")

    class Executor:
        async def execute(self, context, request):
            nonlocal attempts
            attempts += 1
            return ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                retryable=True,
            )

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("not used")

    # Coordinator siết trần chỉ cho chạy tối đa 2 lần tổng cộng
    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        retry_decider=lambda result, attempt: True,
        max_attempts=2,
    )

    result = await coordinator.execute(context, req)
    
    assert attempts == 2
    assert result.retryable is True
    assert result.metadata["attempt"] == 2
    assert context.retry_attempts_used == 1


@pytest.mark.asyncio
async def test_coordinator_capped_by_context_max_retry_attempts():
    """Kiểm tra giới hạn bị siết bởi max_retry_attempts của Context."""
    context = make_context()
    context.limits.max_retry_attempts = 1  # Context chỉ cho phép 1 + 1 = 2 lần chạy
    attempts = 0
    req = request(context, "call-1", "tool.a")

    class Executor:
        async def execute(self, context, request):
            nonlocal attempts
            attempts += 1
            return ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                retryable=True,
            )

        async def execute_many(self, context, requests, *, max_parallel):
            raise AssertionError("not used")

    # Coordinator cho phép tới 5 lần, nhưng bị giới hạn bởi Context
    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        retry_decider=lambda result, attempt: True,
        max_attempts=5,
    )

    result = await coordinator.execute(context, req)

    assert attempts == 2
    assert result.retryable is True
    assert result.metadata["attempt"] == 2
    assert context.retry_attempts_used == 1


def test_coordinator_requires_positive_limits():
    class Executor:
        async def execute(self, context, request):
            return result_for(request)

        async def execute_many(self, context, requests, *, max_parallel):
            return []

    with pytest.raises(ValueError, match="max_attempts"):
        AgentToolExecutionCoordinator(Executor(), max_attempts=0)


@pytest.mark.asyncio
async def test_coordinator_retry_budget_is_execution_scoped():
    context = make_context()
    context.limits.max_retry_attempts = 2

    attempts = 0
    req = request(context, "call-1", "tool.a")

    class Executor:
        async def execute(self, context, request):
            nonlocal attempts
            attempts += 1
            return ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                retryable=True,
            )

    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        retry_decider=lambda result, attempt: True,
    )

    result = await coordinator.execute(context, req)

    assert attempts == 3
    assert context.retry_attempts_used == 2
    assert result.metadata["attempt"] == 3


@pytest.mark.asyncio
async def test_coordinator_deduplicates_concurrent_invocation():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    calls = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return result_for(request)

    coordinator = AgentToolExecutionCoordinator(Executor())

    first, second = await asyncio.gather(
        coordinator.execute(context, req),
        coordinator.execute(context, req),
    )

    assert calls == 1
    assert first == second


@pytest.mark.asyncio
async def test_coordinator_cancellation_cancels_downstream():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Executor:
        async def execute(self, context, request):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    coordinator = AgentToolExecutionCoordinator(Executor())

    task = asyncio.create_task(
        coordinator.execute(context, req)
    )

    await started.wait()

    context.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_coordinator_caller_cancellation_does_not_cancel_shared_execution():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    started = asyncio.Event()
    finished = asyncio.Event()
    allow_finish = asyncio.Event()

    class Executor:
        async def execute(self, context, request):
            started.set()
            try:
                await allow_finish.wait()
            except asyncio.CancelledError:
                finished.set()
                raise
            return result_for(request)

    coordinator = AgentToolExecutionCoordinator(Executor())

    first = asyncio.create_task(coordinator.execute(context, req))
    await started.wait()

    second = asyncio.create_task(coordinator.execute(context, req))
    first.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first

    assert not finished.is_set()

    allow_finish.set()

    result = await asyncio.wait_for(second, timeout=1)
    assert result.tool_call_id == req.tool_call_id
    assert not finished.is_set()


@pytest.mark.asyncio
async def test_coordinator_cancels_shared_execution_when_last_waiter_detaches():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Executor:
        async def execute(self, context, request):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    coordinator = AgentToolExecutionCoordinator(Executor())

    first = asyncio.create_task(coordinator.execute(context, req))
    second = asyncio.create_task(coordinator.execute(context, req))
    await started.wait()

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert not cancelled.is_set()

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second

    await asyncio.wait_for(
        coordinator._wait_for_task_cleanup(context, req),
        timeout=1,
    )

    assert cancelled.is_set()
    assert not coordinator._inflight


@pytest.mark.asyncio
async def test_coordinator_new_caller_waits_for_cancelled_shared_task_before_redispach():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    started = asyncio.Event()
    finished = asyncio.Event()
    active = 0
    peak_active = 0
    calls = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal active, peak_active, calls
            calls += 1
            active += 1
            peak_active = max(peak_active, active)
            started.set()
            try:
                if calls == 1:
                    await asyncio.sleep(10)
                return result_for(request)
            except asyncio.CancelledError:
                finished.set()
                raise
            finally:
                active -= 1

    coordinator = AgentToolExecutionCoordinator(Executor())

    first = asyncio.create_task(coordinator.execute(context, req))
    second = asyncio.create_task(coordinator.execute(context, req))
    await started.wait()

    first.cancel()
    second.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(asyncio.CancelledError):
        await second

    third = asyncio.create_task(coordinator.execute(context, req))
    result = await asyncio.wait_for(third, timeout=1)

    assert result.tool_call_id == req.tool_call_id
    assert calls == 2
    assert peak_active == 1
    assert finished.is_set()


@pytest.mark.asyncio
async def test_coordinator_retry_budget_is_hard_ceiling_over_local_max_attempts():
    context = make_context()
    context.limits.max_retry_attempts = 1
    req = request(context, "call-1", "tool.a")
    attempts = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal attempts
            attempts += 1
            return ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                retryable=True,
            )

    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        retry_decider=lambda result, attempt: True,
        max_attempts=3,
    )

    result = await coordinator.execute(context, req)

    assert attempts == 2
    assert context.retry_attempts_used == 1
    assert result.metadata["attempt"] == 2


@pytest.mark.asyncio
async def test_coordinator_retry_budget_zero_disables_retry_even_with_max_attempts_override():
    context = make_context()
    context.limits.max_retry_attempts = 0
    req = request(context, "call-1", "tool.a")
    attempts = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal attempts
            attempts += 1
            return ToolExecutionResult(
                execution_id=request.execution_id,
                iteration=request.iteration,
                invocation_id=request.invocation_id,
                tool_call_id=request.tool_call_id,
                capability_id=request.capability_id,
                success=False,
                error_code="CAPABILITY_TIMEOUT",
                retryable=True,
            )

    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        retry_decider=lambda result, attempt: True,
        max_attempts=3,
    )

    result = await coordinator.execute(context, req)

    assert attempts == 1
    assert context.retry_attempts_used == 0
    assert result.metadata["attempt"] == 1


@pytest.mark.asyncio
async def test_coordinator_reuses_completed_invocation():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    calls = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal calls
            calls += 1
            return result_for(request)

    coordinator = AgentToolExecutionCoordinator(Executor())

    first = await coordinator.execute(context, req)
    second = await coordinator.execute(context, req)

    assert calls == 1
    assert first == second


@pytest.mark.asyncio
async def test_coordinator_rejects_conflicting_reuse_of_invocation_id():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    conflicting = ToolExecutionRequest(
        execution_id=req.execution_id,
        iteration=req.iteration,
        invocation_id=req.invocation_id,
        tool_call_id=req.tool_call_id,
        capability_id="tool.b",
        arguments={"different": True},
    )

    class Executor:
        async def execute(self, context, request):
            return result_for(request)

    coordinator = AgentToolExecutionCoordinator(Executor())

    first = await coordinator.execute(context, req)
    assert first.capability_id == "tool.a"

    with pytest.raises(
        ValueError,
        match="Conflicting request",
    ):
        await coordinator.execute(context, conflicting)


@pytest.mark.asyncio
async def test_coordinator_removes_cancelled_invocation_from_inflight():
    context = make_context()
    req = request(context, "call-1", "tool.a")

    started = asyncio.Event()

    class Executor:
        async def execute(self, context, request):
            started.set()
            await asyncio.sleep(10)

    coordinator = AgentToolExecutionCoordinator(Executor())

    task = asyncio.create_task(
        coordinator.execute(context, req)
    )

    await started.wait()
    context.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not coordinator._inflight