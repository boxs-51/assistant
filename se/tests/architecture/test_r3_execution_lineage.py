from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.ids import AgentExecutionIdFactory
from se.src.runtimes.capability.composition import DeclarativeWorkflowDriver
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


def _identity() -> Identity:
    return Identity(user_id="user-r3-d", auth_type="api_key", scopes={"*"})


def _factory(token: str) -> AgentExecutionIdFactory:
    return AgentExecutionIdFactory(
        prefix="exec_",
        token_factory=lambda: token,
    )


def _agent_driver(*, token: str = "child"):
    captured = {}
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

    return (
        definition,
        AgentCapabilityDriver(
            definition,
            agent,
            _AgentRuntime(),
            execution_id_factory=_factory(token),
        ),
        captured,
    )


@pytest.mark.asyncio
async def test_d0_direct_agent_capability_creates_root_execution_without_dangling_parent():
    definition, driver, captured = _agent_driver(token="root-agent")
    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store)
    )
    runtime.register_capability(driver)

    result = await runtime.execute_capability(
        definition.capability_id,
        {"prompt": "direct"},
        _identity(),
        invocation_id="inv-direct",
        session_id="session-1",
        connection_id="conn-direct",
    )

    invocation = store.items["inv-direct"]
    child = captured["context"]

    assert result.output["execution_id"] == "exec_root-agent"
    assert child.execution_id == "exec_root-agent"
    assert child.parent_execution_id is None
    assert child.causation_id == "inv-direct"
    assert child.connection_id == "conn-direct"
    assert invocation.execution_id is not None
    assert invocation.execution_id != child.execution_id


@pytest.mark.asyncio
async def test_d0_nested_agent_uses_typed_caller_as_parent_and_invocation_owner():
    definition, driver, captured = _agent_driver()
    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        invocation_lifecycle=CapabilityInvocationLifecycle(store)
    )
    runtime.register_capability(driver)

    await runtime.execute_capability(
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
        connection_id="conn-1",
    )

    invocation = store.items["inv-i1"]
    child = captured["context"]

    assert invocation.execution_id == "exec-e1"
    assert child.execution_id == "exec_child"
    assert child.execution_id != invocation.execution_id
    assert child.parent_execution_id == "exec-e1"
    assert child.task_id == "task-1"
    assert child.branch_id == "branch-1"
    assert child.correlation_id == "corr-1"
    assert child.trace_id == "trace-1"
    assert child.causation_id == "inv-i1"
    assert child.request_id == "request-1"
    assert child.workflow_id == "workflow-1"
    assert child.connection_id == "conn-1"
    assert child.retry_of_execution_id is None
    assert child.base_execution_id is None
    assert child.base_checkpoint_id is None


@pytest.mark.asyncio
async def test_d0_rejects_mismatched_agent_provenance():
    definition, driver, _captured = _agent_driver()
    runtime = CapabilityRuntime()
    runtime.register_capability(driver)

    with pytest.raises(ValueError, match="caller_agent_execution_id"):
        await runtime.execute_capability(
            definition.capability_id,
            {"prompt": "bad lineage"},
            _identity(),
            execution_id="exec-e1",
            caller_agent_execution_id="exec-other",
        )


@pytest.mark.asyncio
async def test_d1_connection_affinity_does_not_create_or_change_parent_lineage():
    definition, driver, captured = _agent_driver(token="direct-with-connection")
    runtime = CapabilityRuntime()
    runtime.register_capability(driver)

    await runtime.execute_capability(
        definition.capability_id,
        {"prompt": "direct"},
        _identity(),
        invocation_id="inv-direct-conn",
        connection_id="conn-99",
    )

    child = captured["context"]
    assert child.connection_id == "conn-99"
    assert child.parent_execution_id is None


def test_d1_non_agent_context_remains_valid_without_agent_provenance():
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-generic",
        invocation_id="inv-generic",
        session_id="session-1",
    )

    assert context.execution_id == "exec-generic"
    assert context.caller_agent_execution_id is None
    assert context.task_id is None
    assert context.branch_id is None


@pytest.mark.asyncio
async def test_d1_composition_preserves_agent_provenance_without_fabricating_it():
    calls = []

    class _Runtime:
        async def execute_capability(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output=kwargs["arguments"]["value"])

    workflow = SimpleNamespace(
        steps=[
            SimpleNamespace(
                step_id="one",
                tool_name="tool.one",
                arguments={"value": "{{initial_input.value}}"},
            )
        ],
        output_template="{{steps.last.output}}",
    )
    driver = DeclarativeWorkflowDriver(
        CapabilityDefinition(
            id="workflow.d1",
            name="workflow.d1",
            description="workflow",
            input_schema={"type": "object"},
            execution_kind="DECLARATIVE",
        ),
        workflow,
        _Runtime(),
    )

    nested_context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-e1",
        invocation_id="inv-workflow",
        caller_agent_execution_id="exec-e1",
    )
    await driver.execute(nested_context, {"value": 1})
    assert calls[-1]["caller_agent_execution_id"] == "exec-e1"

    direct_context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-direct",
        invocation_id="inv-direct-workflow",
    )
    await driver.execute(direct_context, {"value": 2})
    assert calls[-1]["caller_agent_execution_id"] is None
