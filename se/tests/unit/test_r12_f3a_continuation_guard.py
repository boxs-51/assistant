from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.resume import (
    ResumeInvocationAction,
    ResumeInvocationActionKind,
)
from se.src.runtimes.agent.contracts.tool import ToolExecutionResult
from se.src.runtimes.agent.tool_execution.coordinator import (
    AgentToolExecutionCoordinator,
)
from se.src.runtimes.capability.contracts.definition import CapabilityIdempotency
from se.src.runtimes.capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)


def _context():
    return AgentExecutionContext.create(
        execution_id="exec-r12-f3a-guard",
        agent_id="agent-r12-f3a-guard",
        session_id="session-r12-f3a-guard",
        correlation_id="corr-r12-f3a-guard",
        identity=Identity(
            user_id="user-r12-f3a-guard",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(max_parallel_tools=2),
        connection_id="conn-r12-f3a-guard",
        remaining_active_budget_seconds=30.0,
    )


def _resume_action(invocation_id: str, tool_call_id: str):
    return ResumeInvocationAction(
        invocation_id=invocation_id,
        tool_call_id=tool_call_id,
        ordinal=0,
        capability_id="tool.echo",
        capability_version="1.0",
        request_fingerprint=f"fp:{invocation_id}",
        idempotency=CapabilityIdempotency.IDEMPOTENT,
        expected_invocation_revision=3,
        expected_invocation_state=CapabilityInvocationState.CREATED,
        expected_remote_outcome_state=RemoteOutcomeState.NOT_DISPATCHED,
        action=ResumeInvocationActionKind.DISPATCH_NOT_DISPATCHED,
    )


class _Executor:
    def __init__(self):
        self.calls = []
        self.a_started = asyncio.Event()
        self.a_finish = asyncio.Event()
        self.a_completed = False

    async def continue_invocation(self, context, action):
        self.calls.append(action.invocation_id)
        if action.invocation_id == "inv-a":
            self.a_started.set()
            await self.a_finish.wait()
            self.a_completed = True
        return ToolExecutionResult(
            execution_id=context.execution_id,
            iteration=context.iteration,
            invocation_id=action.invocation_id,
            tool_call_id=action.tool_call_id,
            capability_id=action.capability_id,
            success=True,
            output={"invocation_id": action.invocation_id},
        )


@pytest.mark.asyncio
async def test_r12_f3a_default_r7_continuation_path_remains_compatible():
    context = _context()
    context.iteration = 1
    executor = _Executor()
    coordinator = AgentToolExecutionCoordinator(executor)
    action = _resume_action("inv-default", "call-default")

    results = await coordinator.continue_invocations(
        context,
        [action],
        max_parallel=1,
    )

    assert executor.calls == ["inv-default"]
    assert results[0].invocation_id == "inv-default"


@pytest.mark.asyncio
async def test_r12_f3a_guard_failure_preserves_already_started_continuation():
    context = _context()
    context.iteration = 1
    executor = _Executor()
    coordinator = AgentToolExecutionCoordinator(executor)
    raw_a = SimpleNamespace(invocation_id="inv-a", tool_call_id="call-a")
    raw_b = SimpleNamespace(invocation_id="inv-b", tool_call_id="call-b")
    mapped_a = _resume_action("inv-a", "call-a")

    async def prepare(raw_action):
        if raw_action.invocation_id == "inv-a":
            return mapped_a
        await executor.a_started.wait()
        executor.a_finish.set()
        raise RuntimeError("recovery fence lost")

    with pytest.raises(RuntimeError, match="recovery fence lost"):
        await coordinator.continue_invocations(
            context,
            [raw_a, raw_b],
            max_parallel=2,
            pre_dispatch_prepare=prepare,
            preserve_started_on_failure=True,
        )

    assert executor.calls == ["inv-a"]
    assert executor.a_completed is True
