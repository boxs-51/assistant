import asyncio

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionState
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.state_machine import AgentExecutionStateMachine


def make_identity():
    return Identity(user_id="user-1", organization_id="org-1", auth_type="api_key")


@pytest.mark.asyncio
async def test_task_execution_records_completed_execution():
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="worker", goal="Work", instruction="Work"))
    coordinator = MultiAgentCoordinator(registry)
    session = coordinator.create_session(make_identity(), ["worker"])
    task = coordinator.create_task(session.session_id, "worker", {"value": "ok"}, make_identity())

    async def executor(task):
        return {"output": task.input["value"]}

    execution = await coordinator.execute_task(task.task_id, make_identity(), executor)

    assert execution.state is AgentExecutionState.COMPLETED
    assert execution.result == {"output": "ok"}
    assert coordinator.get_execution(execution.execution_id, make_identity()) == execution


@pytest.mark.asyncio
async def test_task_execution_propagates_identity_to_canonical_executor():
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="worker", goal="Work", instruction="Work"))
    coordinator = MultiAgentCoordinator(registry)
    identity = make_identity()
    session = coordinator.create_session(identity, ["worker"])
    task = coordinator.create_task(session.session_id, "worker", {}, identity)

    async def executor(task, *, identity):
        return {"task_id": task.task_id, "user_id": identity.user_id}

    execution = await coordinator.execute_task(task.task_id, identity, executor)

    assert execution.state is AgentExecutionState.COMPLETED
    assert execution.result["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_background_task_can_be_polled_and_cancelled():
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="worker", goal="Work", instruction="Work"))
    coordinator = MultiAgentCoordinator(registry)
    identity = make_identity()
    session = coordinator.create_session(identity, ["worker"])
    task = coordinator.create_task(session.session_id, "worker", {}, identity)

    async def executor(_task, *, identity):
        await asyncio.Event().wait()

    started = await coordinator.start_task(task.task_id, identity, executor)
    assert started.status.value == "RUNNING"

    cancelled = coordinator.cancel_task(task.task_id, identity)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert cancelled.status.value == "CANCELLED"
    assert coordinator.get_task(task.task_id, identity).status.value == "CANCELLED"


@pytest.mark.asyncio
async def test_task_execution_times_out():
    registry = AgentRegistry()
    registry.register(AgentDefinition(name="worker", goal="Work", instruction="Work"))
    coordinator = MultiAgentCoordinator(registry)
    session = coordinator.create_session(make_identity(), ["worker"])
    task = coordinator.create_task(session.session_id, "worker", {}, make_identity())

    async def executor(_task):
        await asyncio.sleep(0.05)
        return {"done": True}

    from se.src.domain.schemas.agent_execution import AgentExecutionLimits
    execution = await coordinator.execute_task(
        task.task_id,
        make_identity(),
        executor,
        limits=AgentExecutionLimits(timeout_seconds=0.001),
    )

    assert execution.state is AgentExecutionState.TIMEOUT


def test_execution_state_machine_rejects_terminal_restart():
    with pytest.raises(ValueError):
        AgentExecutionStateMachine.transition(
            AgentExecutionState.COMPLETED,
            AgentExecutionState.RUNNING,
        )
