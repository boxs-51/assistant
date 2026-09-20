from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.ids import AgentExecutionIdFactory
from se.src.runtimes.capability.contracts.context import (
    CapabilityExecutionContext,
)
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.agent_driver import (
    AgentCapabilityDriver,
)


def _identity() -> Identity:
    return Identity(
        user_id="user-r4",
        auth_type="api_key",
        scopes={"*"},
    )


def _factory():
    return AgentExecutionIdFactory(
        prefix="exec_",
        token_factory=lambda: "child",
    )


def _driver(captured):
    agent = AgentDefinition(
        name="agent-child",
        goal="child",
        instruction="child",
    )
    definition = CapabilityDefinition(
        id=agent.name,
        name=agent.name,
        description=agent.goal,
        kind=CapabilityKind.AGENT,
        execution_mode=CapabilityExecutionMode.LONG_RUNNING,
    )

    class _AgentRuntime:
        async def execute(self, context):
            captured["context"] = context
            return SimpleNamespace(
                error_code=None,
                error_message=None,
                model_dump=lambda mode=None: {
                    "execution_id": context.execution_id,
                    "output": "done",
                },
            )

    return AgentCapabilityDriver(
        definition,
        agent,
        _AgentRuntime(),
        execution_id_factory=_factory(),
    )


@pytest.mark.asyncio
async def test_r4_c3_nested_child_is_bounded_by_parent_iteration():
    captured = {}
    driver = _driver(captured)
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-parent",
        invocation_id="inv-child",
        caller_agent_execution_id="exec-parent",
        caller_execution_remaining_seconds=12.0,
        caller_iteration_remaining_seconds=7.0,
        session_id="session-r4",
        task_id="task-r4",
        branch_id="branch-r4",
        correlation_id="corr-r4",
    )

    output = await driver.execute(context, {"prompt": "delegate"})
    child = captured["context"]

    assert output["execution_id"] == "exec_child"
    assert child.execution_id == "exec_child"
    assert child.parent_execution_id == "exec-parent"
    assert child.limits.timeout_seconds == 7.0
    assert child.remaining_active_budget_seconds == 7.0
    assert child.remaining_seconds <= 7.0
    assert child.causation_id == "inv-child"


@pytest.mark.asyncio
async def test_r4_c3_parent_execution_can_be_tighter_than_parent_iteration():
    captured = {}
    driver = _driver(captured)
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-parent",
        invocation_id="inv-child",
        caller_agent_execution_id="exec-parent",
        caller_execution_remaining_seconds=5.0,
        caller_iteration_remaining_seconds=7.0,
        session_id="session-r4",
        correlation_id="corr-r4",
    )

    await driver.execute(context, {})
    child = captured["context"]

    assert child.limits.timeout_seconds == 5.0
    assert child.remaining_active_budget_seconds == 5.0


@pytest.mark.asyncio
async def test_r4_c3_direct_agent_without_parent_keeps_own_configured_budget():
    captured = {}
    driver = _driver(captured)
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="direct-capability-exec",
        invocation_id="inv-direct",
        caller_agent_execution_id=None,
        session_id="session-r4",
        correlation_id="corr-r4",
    )

    await driver.execute(context, {})
    child = captured["context"]

    assert child.parent_execution_id is None
    assert child.limits.timeout_seconds == 60.0
    assert child.remaining_active_budget_seconds == 60.0


@pytest.mark.asyncio
async def test_r4_c3_external_invocation_window_can_tighten_direct_agent():
    captured = {}
    driver = _driver(captured)
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="direct-capability-exec",
        invocation_id="inv-direct",
        caller_agent_execution_id=None,
        session_id="session-r4",
        correlation_id="corr-r4",
        timeout_seconds=3.0,
    )

    await driver.execute(context, {})
    child = captured["context"]

    assert 0.0 < child.limits.timeout_seconds <= 3.0
    assert (
        child.remaining_active_budget_seconds
        == child.limits.timeout_seconds
    )
