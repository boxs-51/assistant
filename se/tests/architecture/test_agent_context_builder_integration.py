from __future__ import annotations

import asyncio
import pytest
from pydantic import ValidationError

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.context import ContextBuilderAdapter
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.agent.contracts import (
    AgentContextRequest,
    AgentExecutionContext,
    AgentLoopState,
    InferenceMessage,
    ToolExecutionResult,
)
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from se.src.agent.registry import AgentRegistry

class ContextEngine:
    async def load_context(self, session_id, identity):
        class Session:
            messages = [InferenceMessage(role="user", content="history")]

        class Loaded:
            session = Session()

        return Loaded()


class ContextRuntime:
    context_engine = ContextEngine()


def make_context(*, tools=None):
    agent = AgentDefinition(
        name="phase5-11-agent",
        goal="context gate",
        instruction="system instruction",
        tools=tools or [],
    )
    return AgentExecutionContext.create(
        execution_id="exec-phase5-11",
        agent_id=agent.name,
        session_id="session-phase5-11",
        correlation_id="corr-phase5-11",
        identity=Identity(user_id="u1", auth_type="api_key", scopes={"*"}),
        limits=AgentExecutionLimits(max_iterations=2),
        metadata={"request_id": "req-1"},
        trace_id="trace-1",
        agent=agent,
        input={"prompt": "current prompt"},
    )


def make_adapter(context):
    registry = CapabilityRegistry()
    registry.register_capability(
        PythonCapabilityDriver(
            CapabilityDefinition(
                id="calculator.add",
                name="calculator.add",
                description="Add values",
                input_schema={"type": "object"},
            ),
            lambda **kwargs: 3,
        )
    )
    agent_registry = AgentRegistry()
    agent_registry.register(context.agent)
    policy = RegistryAgentToolPolicy(
        agent_registry,
        registry,
        AuthorizationService(),
    )
    return ContextBuilderAdapter(
        ContextRuntime(),
        CapabilityRuntime(registry=registry, authorization=AuthorizationService()),
        policy,
    )


@pytest.mark.asyncio
async def test_context_snapshot_is_loaded_per_iteration_and_immutable():
    context = make_context(tools=["calculator.add"])
    adapter = make_adapter(context)
    snapshot = await adapter.build(
        context,
        AgentContextRequest(
            execution_id=context.execution_id,
            iteration=1,
            metadata={"iteration_key": "value"},
        ),
    )

    assert snapshot.execution_id == context.execution_id
    assert snapshot.iteration == 1
    assert snapshot.messages[0].role == "system"
    assert snapshot.messages[-1].content == "current prompt"
    assert isinstance(snapshot.messages, tuple)
    assert isinstance(snapshot.tools, tuple)
    assert snapshot.metadata["agent_id"] == context.agent_id
    assert snapshot.metadata["session_id"] == context.session_id
    assert snapshot.metadata["trace_id"] == "trace-1"
    with pytest.raises(ValidationError):
        snapshot.iteration = 2


@pytest.mark.asyncio
async def test_tool_results_are_composed_as_inference_messages():
    context = make_context()
    adapter = make_adapter(context)
    result = ToolExecutionResult(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="calculator.add",
        success=True,
        output={"value": 3},
    )

    snapshot = await adapter.build(
        context,
        AgentContextRequest(
            execution_id=context.execution_id,
            iteration=1,
            tool_results=[result],
        ),
    )

    tool_message = snapshot.messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.content == {"value": 3}


@pytest.mark.asyncio
async def test_policy_filters_unregistered_or_unauthorized_tools():
    context = make_context(tools=["calculator.add", "missing.tool"])
    adapter = make_adapter(context)
    snapshot = await adapter.build(
        context,
        AgentContextRequest(execution_id=context.execution_id, iteration=1),
    )

    assert [tool.name for tool in snapshot.tools] == ["calculator.add"]


@pytest.mark.asyncio
async def test_identity_mismatch_and_cancellation_fail_closed():
    context = make_context()
    adapter = make_adapter(context)

    with pytest.raises(ValueError, match="execution_id"):
        await adapter.build(
            context,
            AgentContextRequest(execution_id="other", iteration=1),
        )

    context.cancel()
    with pytest.raises(asyncio.CancelledError):
        await adapter.build(
            context,
            AgentContextRequest(execution_id=context.execution_id, iteration=1),
        )


