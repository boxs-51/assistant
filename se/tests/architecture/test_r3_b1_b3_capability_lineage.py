from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.workflow import WorkflowDefinition, WorkflowStep
from se.src.runtimes.agent.adapters.policy import (
    DefaultAgentExecutionPolicy,
    RegistryAgentToolPolicy,
)
from se.src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.tool import ToolExecutionRequest
from se.src.runtimes.capability.composition import DeclarativeWorkflowDriver
from se.src.runtimes.capability.contracts.context import CapabilityExecutionContext
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.drivers.remote_client_driver import RemoteClientDriver
from se.src.runtimes.capability.drivers.skill_driver import (
    ExecutableSkillCapabilityDriver,
)
from se.src.runtimes.capability.invocation import (
    CapabilityInvocationLifecycle,
    InMemoryCapabilityInvocationStore,
)
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime


def _identity() -> Identity:
    return Identity(user_id="user-r3", auth_type="api_key", scopes={"*"})


class _CaptureDriver(BaseCapabilityDriver):
    def __init__(self, definition: CapabilityDefinition) -> None:
        super().__init__(definition)
        self.contexts = []

    async def execute(self, context, arguments):
        self.contexts.append(context)
        return {"ok": True, "arguments": dict(arguments)}


def test_b1_capability_context_supports_typed_agent_lineage_and_direct_nulls():
    agent_owned = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-e1",
        invocation_id="inv-i1",
        caller_agent_execution_id="exec-e1",
        session_id="session-1",
        task_id="task-1",
        branch_id="branch-1",
        correlation_id="corr-1",
        trace_id="trace-1",
    )
    direct = CapabilityExecutionContext.create(identity=_identity())

    assert agent_owned.execution_id == "exec-e1"
    assert agent_owned.invocation_id == "inv-i1"
    assert agent_owned.caller_agent_execution_id == "exec-e1"
    assert agent_owned.task_id == "task-1"
    assert agent_owned.branch_id == "branch-1"
    assert agent_owned.correlation_id == "corr-1"
    assert agent_owned.trace_id == "trace-1"

    assert direct.task_id is None
    assert direct.branch_id is None
    assert direct.caller_agent_execution_id is None
    assert direct.correlation_id is None
    assert direct.trace_id is None


@pytest.mark.asyncio
async def test_b2_runtime_keeps_invocation_owned_by_e1_and_typed_ids_win_metadata():
    definition = CapabilityDefinition(
        id="tool.capture",
        name="tool.capture",
        description="capture context",
        input_schema={"type": "object"},
    )
    driver = _CaptureDriver(definition)
    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
    )
    runtime.register_capability(driver)

    result = await runtime.execute_capability(
        capability_id=definition.capability_id,
        arguments={"value": 1},
        identity=_identity(),
        execution_id="exec-e1",
        invocation_id="inv-i1",
        request_id="request-1",
        session_id="session-1",
        task_id="task-1",
        branch_id="branch-1",
        correlation_id="corr-typed",
        trace_id="trace-typed",
        workflow_id="workflow-1",
        metadata={
            "correlation_id": "corr-legacy",
            "trace_id": "trace-legacy",
        },
    )

    assert result.invocation_id == "inv-i1"
    assert len(driver.contexts) == 1
    context = driver.contexts[0]
    assert context.execution_id == "exec-e1"
    assert context.invocation_id == "inv-i1"
    assert context.task_id == "task-1"
    assert context.branch_id == "branch-1"
    assert context.correlation_id == "corr-typed"
    assert context.trace_id == "trace-typed"

    persisted = store.items["inv-i1"]
    assert persisted.execution_id == "exec-e1"
    assert persisted.correlation_id == "corr-typed"
    assert persisted.trace_id == "trace-typed"


@pytest.mark.asyncio
async def test_b2_runtime_keeps_metadata_fallback_for_legacy_correlation():
    definition = CapabilityDefinition(
        id="tool.legacy",
        name="tool.legacy",
        description="legacy metadata fallback",
        input_schema={"type": "object"},
    )
    driver = _CaptureDriver(definition)
    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
    )
    runtime.register_capability(driver)

    await runtime.execute_capability(
        capability_id=definition.capability_id,
        arguments={},
        identity=_identity(),
        execution_id="exec-legacy",
        invocation_id="inv-legacy",
        metadata={
            "correlation_id": "corr-legacy",
            "trace_id": "trace-legacy",
        },
    )

    context = driver.contexts[0]
    persisted = store.items["inv-legacy"]
    assert context.correlation_id == "corr-legacy"
    assert context.trace_id == "trace-legacy"
    assert persisted.correlation_id == "corr-legacy"
    assert persisted.trace_id == "trace-legacy"


@pytest.mark.asyncio
async def test_b3_agent_tool_adapter_passes_caller_lineage_to_capability_runtime():
    definition = CapabilityDefinition(
        id="tool.agent-lineage",
        name="tool.agent-lineage",
        description="capture agent caller lineage",
        input_schema={"type": "object"},
    )
    driver = _CaptureDriver(definition)
    registry = CapabilityRegistry()
    store = InMemoryCapabilityInvocationStore()
    runtime = CapabilityRuntime(
        registry=registry,
        authorization=AuthorizationService(),
        invocation_lifecycle=CapabilityInvocationLifecycle(store),
    )
    runtime.register_capability(driver)

    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            name="agent-parent",
            goal="test",
            instruction="test",
            tools=[definition.capability_id],
        )
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        RegistryAgentToolPolicy(
            agents,
            registry,
            AuthorizationService(),
        ),
        DefaultAgentExecutionPolicy(),
    )
    context = AgentExecutionContext.create(
        execution_id="exec-e1",
        agent_id="agent-parent",
        session_id="session-1",
        correlation_id="corr-1",
        identity=_identity(),
        limits=AgentExecutionLimits(timeout_seconds=5),
        request_id="request-1",
        task_id="task-1",
        branch_id="branch-1",
        workflow_id="workflow-1",
        trace_id="trace-1",
        input={"prompt": "hello"},
        agent=agents.get("agent-parent"),
    )
    request = ToolExecutionRequest(
        execution_id="exec-e1",
        iteration=1,
        invocation_id="inv-i1",
        tool_call_id="call-1",
        capability_id=definition.capability_id,
        arguments={"value": 1},
    )

    result = await adapter.execute(context, request)

    assert result.success is True
    captured = driver.contexts[0]
    assert captured.execution_id == "exec-e1"
    assert captured.invocation_id == "inv-i1"
    assert captured.task_id == "task-1"
    assert captured.branch_id == "branch-1"
    assert captured.correlation_id == "corr-1"
    assert captured.trace_id == "trace-1"
    assert captured.request_id == "request-1"
    assert captured.session_id == "session-1"
    assert captured.workflow_id == "workflow-1"

    persisted = store.items["inv-i1"]
    assert persisted.execution_id == "exec-e1"
    assert persisted.correlation_id == "corr-1"
    assert persisted.trace_id == "trace-1"


@pytest.mark.asyncio
async def test_b3_composition_propagates_lineage_and_leaves_step_invocation_allocation_to_runtime():
    calls = []

    class _Runtime:
        async def execute_capability(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output=kwargs["arguments"]["value"])

    workflow = WorkflowDefinition(
        steps=[
            WorkflowStep(
                step_id="one",
                tool_name="tool.one",
                arguments={"value": "{{initial_input.value}}"},
            )
        ],
        output_template="{{steps.last.output}}",
    )
    driver = DeclarativeWorkflowDriver(
        CapabilityDefinition(
            id="workflow.lineage",
            name="workflow.lineage",
            description="workflow lineage",
            input_schema={"type": "object"},
            execution_kind="DECLARATIVE",
        ),
        workflow,
        _Runtime(),
    )
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-e1",
        invocation_id="inv-workflow",
        caller_agent_execution_id="exec-e1",
        request_id="request-1",
        session_id="session-1",
        task_id="task-1",
        branch_id="branch-1",
        correlation_id="corr-1",
        trace_id="trace-1",
        workflow_id="workflow-1",
    )

    output = await driver.execute(context, {"value": 7})

    assert output == 7
    assert len(calls) == 1
    call = calls[0]
    assert call["execution_id"] == "exec-e1"
    assert call["caller_agent_execution_id"] == "exec-e1"
    assert call["task_id"] == "task-1"
    assert call["branch_id"] == "branch-1"
    assert call["correlation_id"] == "corr-1"
    assert call["trace_id"] == "trace-1"
    assert call["request_id"] == "request-1"
    assert call["session_id"] == "session-1"
    assert call["workflow_id"] == "workflow-1"
    assert "invocation_id" not in call


@pytest.mark.asyncio
async def test_b3_remote_driver_prefers_typed_trace_over_legacy_metadata():
    class _Realtime:
        def __init__(self):
            self.envelope = None

        async def invoke(self, envelope, timeout=None):
            self.envelope = envelope
            return {"ok": True}

        async def cancel(self, connection_id, invocation_id):
            return None

    realtime = _Realtime()
    driver = RemoteClientDriver(
        CapabilityDefinition(
            id="remote.tool",
            name="remote.tool",
            description="remote",
            input_schema={"type": "object"},
        ),
        realtime,
        "conn-1",
    )
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-e1",
        invocation_id="inv-i1",
        session_id="session-1",
        connection_id="conn-1",
        trace_id="trace-typed",
        metadata={"trace_id": "trace-legacy"},
    )

    assert await driver.execute(context, {"value": 1}) == {"ok": True}
    assert realtime.envelope.execution_id == "exec-e1"
    assert realtime.envelope.invocation_id == "inv-i1"
    assert realtime.envelope.trace_id == "trace-typed"


@pytest.mark.asyncio
async def test_b3_executable_skill_stays_inside_e1_and_exposes_typed_diagnostics():
    class _Inference:
        def __init__(self):
            self.request = None

        async def complete(self, request):
            self.request = request
            return SimpleNamespace(
                message=SimpleNamespace(
                    model_dump=lambda mode="json": {"role": "assistant", "content": "ok"}
                ),
                usage=SimpleNamespace(model_dump=lambda mode="json": {}),
                provider="test",
                model="test",
            )

    inference = _Inference()
    driver = ExecutableSkillCapabilityDriver(
        CapabilityDefinition(
            id="skill.one",
            name="skill.one",
            description="skill",
            input_schema={"type": "object"},
            execution_kind="SKILL",
        ),
        "Do the skill.",
        inference,
    )
    context = CapabilityExecutionContext.create(
        identity=_identity(),
        execution_id="exec-e1",
        invocation_id="inv-skill",
        task_id="task-1",
        branch_id="branch-1",
        correlation_id="corr-1",
        trace_id="trace-1",
        metadata={"model": "test"},
    )

    await driver.execute(context, {"prompt": "hello"})

    request = inference.request
    assert request.execution_id == "exec-e1"
    assert request.metadata["invocation_id"] == "inv-skill"
    assert request.metadata["task_id"] == "task-1"
    assert request.metadata["branch_id"] == "branch-1"
    assert request.metadata["correlation_id"] == "corr-1"
    assert request.metadata["trace_id"] == "trace-1"
