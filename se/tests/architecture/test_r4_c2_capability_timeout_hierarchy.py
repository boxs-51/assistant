from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.policy import PolicyDecision
from se.src.runtimes.agent.contracts.tool import ToolExecutionRequest
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)


class _FakeClock:
    def __init__(self) -> None:
        self.mono = 100.0

    def monotonic(self) -> float:
        return self.mono

    def now_utc(self):
        return datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


class _AllowToolPolicy:
    def is_visible(self, **kwargs):
        return True

    def authorize(self, **kwargs):
        return PolicyDecision.ALLOW


class _AllowExecutionPolicy:
    def check_tool_call(self, context, request):
        return PolicyDecision.ALLOW


class _Registry:
    def __init__(self, definition):
        self.definition = definition

    def get(self, capability_id):
        if capability_id != self.definition.capability_id:
            return None
        return SimpleNamespace(
            definition=self.definition,
            executable=True,
        )


class _Runtime:
    def __init__(self, definition):
        self.registry = _Registry(definition)
        self.catalog = None
        self.kwargs = None

    async def execute_capability(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            invocation_id=kwargs["invocation_id"],
            output={"ok": True},
            metadata={},
        )


def _context() -> AgentExecutionContext:
    clock = _FakeClock()
    context = AgentExecutionContext.create(
        execution_id="exec-parent",
        agent_id="agent-parent",
        session_id="session-r4",
        correlation_id="corr-r4",
        identity=Identity(
            user_id="user-r4",
            auth_type="api_key",
            scopes={"*"},
        ),
        limits=AgentExecutionLimits(
            timeout_seconds=12.0,
            iteration_timeout_seconds=7.0,
            tool_timeout_seconds=2.0,
        ),
        clock=clock,
    )
    context.begin_iteration_budget()
    return context


def _request(capability_id: str) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        execution_id="exec-parent",
        iteration=1,
        invocation_id=f"inv-{capability_id}",
        tool_call_id=f"call-{capability_id}",
        capability_id=capability_id,
        arguments={},
    )


@pytest.mark.asyncio
async def test_r4_c2_normal_tool_uses_exec_iteration_and_tool_minimum():
    definition = CapabilityDefinition(
        id="tool.normal",
        name="tool.normal",
        description="normal",
        kind=CapabilityKind.TOOL,
        execution_mode=CapabilityExecutionMode.ONE_SHOT,
        input_schema={"type": "object"},
    )
    runtime = _Runtime(definition)
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        _AllowToolPolicy(),
        _AllowExecutionPolicy(),
    )
    context = _context()

    result = await adapter.execute(context, _request("tool.normal"))

    assert result.success is True
    assert runtime.kwargs["timeout_seconds"] == 2.0
    assert runtime.kwargs["caller_execution_remaining_seconds"] == 12.0
    assert runtime.kwargs["caller_iteration_remaining_seconds"] == 7.0


@pytest.mark.asyncio
async def test_r4_c2_agent_ignores_one_shot_tool_timeout_but_keeps_parent_bounds():
    definition = CapabilityDefinition(
        id="agent.child",
        name="agent.child",
        description="child",
        kind=CapabilityKind.AGENT,
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
        input_schema={"type": "object"},
    )
    runtime = _Runtime(definition)
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        _AllowToolPolicy(),
        _AllowExecutionPolicy(),
    )
    context = _context()

    result = await adapter.execute(context, _request("agent.child"))

    assert result.success is True
    assert runtime.kwargs["timeout_seconds"] == 7.0
    assert runtime.kwargs["timeout_seconds"] != 2.0
    assert runtime.kwargs["caller_execution_remaining_seconds"] == 12.0
    assert runtime.kwargs["caller_iteration_remaining_seconds"] == 7.0


@pytest.mark.asyncio
async def test_r4_c2_long_running_non_agent_also_ignores_tool_timeout():
    definition = CapabilityDefinition(
        id="skill.long",
        name="skill.long",
        description="long",
        kind=CapabilityKind.SKILL,
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
        input_schema={"type": "object"},
    )
    runtime = _Runtime(definition)
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        _AllowToolPolicy(),
        _AllowExecutionPolicy(),
    )
    context = _context()

    await adapter.execute(context, _request("skill.long"))

    assert runtime.kwargs["timeout_seconds"] == 7.0
