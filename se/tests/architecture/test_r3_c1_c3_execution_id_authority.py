from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.contracts import (
    AgentExecutionResult,
    AgentLoopState,
    InferenceMessage,
)
from se.src.runtimes.agent.coordinator import MultiAgentCoordinator
from se.src.runtimes.agent.ids import AgentExecutionIdFactory
from se.src.runtimes.capability.contracts.context import CapabilityExecutionContext
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from se.src.runtimes.capability.drivers.agent_driver import AgentCapabilityDriver
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.workflow.runtime import WorkflowRuntime


def _identity() -> Identity:
    return Identity(user_id="user-r3", auth_type="api_key", scopes={"*"})


def _factory(*tokens: str) -> AgentExecutionIdFactory:
    values = iter(tokens)
    return AgentExecutionIdFactory(
        prefix="exec_",
        token_factory=lambda: next(values),
    )


def test_c1_execution_id_factory_is_deterministic_and_independent():
    factory = _factory("one", "two")

    assert factory.new_id() == "exec_one"
    assert factory.new_id() == "exec_two"


@pytest.mark.asyncio
async def test_c2_multi_agent_coordinator_allocates_root_execution_once():
    registry = AgentRegistry()
    registry.register(
        AgentDefinition(name="worker", goal="Work", instruction="Work")
    )
    coordinator = MultiAgentCoordinator(
        registry,
        execution_id_factory=_factory("root-task"),
    )
    identity = _identity()
    session = coordinator.create_session(identity, ["worker"])
    task = coordinator.create_task(
        session.session_id,
        "worker",
        {"prompt": "hello"},
        identity,
    )
    received = {}

    async def executor(
        task,
        *,
        identity,
        execution_id,
        correlation_id,
        parent_execution_id=None,
    ):
        received["execution_id"] = execution_id
        return {"ok": True}

    execution = await coordinator.execute_task(
        task.task_id,
        identity,
        executor,
    )

    assert execution.execution_id == "exec_root-task"
    assert received["execution_id"] == "exec_root-task"


@pytest.mark.asyncio
async def test_c2_workflow_root_agent_uses_injected_execution_factory():
    factory = _factory("root-chat")
    runtime = WorkflowRuntime(factory)
    agent = AgentDefinition(
        name="agent-root",
        goal="test",
        instruction="test",
    )
    captured = {}

    class _AgentRuntime:
        async def execute(self, context):
            captured["context"] = context
            return AgentExecutionResult(
                execution_id=context.execution_id,
                agent_id=context.agent_id,
                state=AgentLoopState.COMPLETED,
                final_message=InferenceMessage(
                    role="assistant",
                    content="done",
                ),
            )

    class _Bus:
        async def publish(self, event):
            return None

    runtime.event_bus = _Bus()
    runtime.container = SimpleNamespace(
        capability_runtime=None,
        agent_registry=SimpleNamespace(get=lambda agent_id: agent),
        agent_runtime=_AgentRuntime(),
    )
    event = BaseEvent(
        event_name="context.event.built",
        session_id="session-1",
        turn_id="turn-1",
        payload={"identity": _identity()},
    )
    body = {
        "agent_id": "agent-root",
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {},
        "config": {},
    }

    await runtime._execute_agent(event, body)

    assert captured["context"].execution_id == "exec_root-chat"
    assert captured["context"].parent_execution_id is None


@pytest.mark.asyncio
async def test_c3_agent_driver_creates_e2_with_delegation_lineage():
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
    captured = {}

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

    driver = AgentCapabilityDriver(
        definition,
        agent,
        _AgentRuntime(),
        execution_id_factory=_factory("child"),
    )
    parent_cancel = __import__("asyncio").Event()
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-e1",
        invocation_id="inv-i1",
        caller_agent_execution_id="exec-e1",
        request_id="request-1",
        session_id="session-1",
        task_id="task-1",
        branch_id="branch-1",
        correlation_id="corr-1",
        trace_id="trace-1",
        connection_id="conn-1",
        workflow_id="workflow-1",
        cancellation_event=parent_cancel,
    )

    output = await driver.execute(context, {"prompt": "delegate"})
    child = captured["context"]

    assert output["execution_id"] == "exec_child"
    assert child.execution_id == "exec_child"
    assert child.execution_id != "exec-e1"
    assert child.parent_execution_id == "exec-e1"
    assert child.causation_id == "inv-i1"
    assert child.task_id == "task-1"
    assert child.branch_id == "branch-1"
    assert child.correlation_id == "corr-1"
    assert child.trace_id == "trace-1"
    assert child.request_id == "request-1"
    assert child.session_id == "session-1"
    assert child.workflow_id == "workflow-1"
    assert child.connection_id == "conn-1"
    assert child.retry_of_execution_id is None
    assert child.base_execution_id is None
    assert child.base_checkpoint_id is None
    assert child.cancellation_event is not parent_cancel


@pytest.mark.asyncio
async def test_c3_capability_invocation_remains_owned_by_e1_while_child_is_e2():
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
    captured = {}

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

    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store)
    )
    runtime.register_capability(
        AgentCapabilityDriver(
            definition,
            agent,
            _AgentRuntime(),
            execution_id_factory=_factory("child"),
        )
    )

    result = await runtime.execute_capability(
        definition.capability_id,
        {"prompt": "delegate"},
        _identity(),
        execution_id="exec-e1",
        caller_agent_execution_id="exec-e1",
        invocation_id="inv-i1",
        request_id="request-1",
        session_id="session-1",
        task_id="task-1",
        branch_id="branch-1",
        correlation_id="corr-1",
        trace_id="trace-1",
        workflow_id="workflow-1",
    )

    invocation = store.items["inv-i1"]
    child = captured["context"]
    assert invocation.execution_id == "exec-e1"
    assert invocation.invocation_id == "inv-i1"
    assert child.execution_id == "exec_child"
    assert child.parent_execution_id == "exec-e1"
    assert child.causation_id == "inv-i1"
    assert result.output["execution_id"] == "exec_child"
