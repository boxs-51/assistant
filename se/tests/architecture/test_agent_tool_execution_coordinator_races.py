from __future__ import annotations

import asyncio

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentExecutionContext,
    ToolExecutionRequest,
    ToolExecutionResult,
)
from se.src.runtimes.agent.tool_execution import AgentToolExecutionCoordinator


def _context() -> AgentExecutionContext:
    agent = AgentDefinition(
        name="coordinator-race-agent",
        goal="tool execution race regression",
        instruction="test",
        tools=["tool.a"],
    )
    return AgentExecutionContext.create(
        execution_id="exec-coordinator-race",
        agent_id=agent.name,
        session_id="session-coordinator-race",
        correlation_id="corr-coordinator-race",
        identity=Identity(user_id="u1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=16,
            max_parallel_tools=4,
            max_retry_attempts=0,
            timeout_seconds=5,
            tool_timeout_seconds=1,
        ),
        agent=agent,
    )


def _request(
    context: AgentExecutionContext,
    *,
    call_id: str,
    invocation_id: str | None = None,
) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id=invocation_id or f"inv-{call_id}",
        tool_call_id=call_id,
        capability_id="tool.a",
        arguments={},
    )


def _result(request: ToolExecutionRequest) -> ToolExecutionResult:
    return ToolExecutionResult(
        execution_id=request.execution_id,
        iteration=request.iteration,
        invocation_id=request.invocation_id,
        tool_call_id=request.tool_call_id,
        capability_id=request.capability_id,
        success=True,
        output=f"result:{request.tool_call_id}",
    )


@pytest.mark.asyncio
async def test_completed_shared_invocation_is_committed_when_waiter_is_cancelled_late():
    context = _context()
    request = _request(context, call_id="call-complete-cancel")
    dispatches = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal dispatches
            dispatches += 1
            await asyncio.sleep(0)
            return _result(request)

    coordinator = AgentToolExecutionCoordinator(Executor())
    original_wait = coordinator._await_with_cancellation

    async def cancel_after_shared_completion(execution_context, task):
        result = await original_wait(execution_context, task)
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        return result

    coordinator._await_with_cancellation = cancel_after_shared_completion
    caller = asyncio.create_task(coordinator.execute(context, request))
    with pytest.raises(asyncio.CancelledError):
        await caller

    coordinator._await_with_cancellation = original_wait
    reused = await coordinator.execute(context, request)

    assert reused.output == "result:call-complete-cancel"
    assert dispatches == 1
    assert (context.execution_id, request.invocation_id) in coordinator._completed


@pytest.mark.asyncio
async def test_completion_cancel_race_never_redispatches_stable_invocation():
    context = _context()
    request = _request(
        context,
        call_id="call-stable",
        invocation_id="stable-invocation",
    )
    dispatches = 0

    class Executor:
        async def execute(self, context, request):
            nonlocal dispatches
            dispatches += 1
            await asyncio.sleep(0)
            return _result(request)

    coordinator = AgentToolExecutionCoordinator(Executor())
    original_wait = coordinator._await_with_cancellation

    async def cancel_after_shared_completion(execution_context, task):
        result = await original_wait(execution_context, task)
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        return result

    coordinator._await_with_cancellation = cancel_after_shared_completion
    caller = asyncio.create_task(coordinator.execute(context, request))
    with pytest.raises(asyncio.CancelledError):
        await caller

    coordinator._await_with_cancellation = original_wait
    reused = await coordinator.execute(context, request)

    assert reused.output == "result:call-stable"
    assert dispatches == 1


@pytest.mark.asyncio
async def test_completed_invocation_ledger_has_bounded_retention():
    context = _context()

    class Executor:
        async def execute(self, context, request):
            return _result(request)

    coordinator = AgentToolExecutionCoordinator(
        Executor(),
        max_completed_entries=2,
    )

    for index in range(3):
        request = _request(
            context,
            call_id=f"call-retention-{index}",
            invocation_id=f"inv-retention-{index}",
        )
        await coordinator.execute(context, request)

    assert len(coordinator._completed) <= 2
    assert (context.execution_id, "inv-retention-0") not in coordinator._completed
