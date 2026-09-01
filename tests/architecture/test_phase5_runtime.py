from __future__ import annotations

import asyncio

import pytest

from src.agent.registry import AgentRegistry
from src.application.policy.authorization import AuthorizationService
from src.domain.schemas.agent import AgentDefinition
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity
from src.runtimes.agent.adapters.context import ContextBuilderAdapter
from src.runtimes.agent.adapters.policy import (
    DefaultAgentExecutionPolicy,
    RegistryAgentToolPolicy,
)
from src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from src.runtimes.agent.contracts import (
    AgentContextRequest,
    AgentExecutionContext,
    AgentLoopState,
    InferenceMessage,
    InferenceResponse,
    InferenceToolCall,
    InferenceUsage,
)
from src.runtimes.agent.runtime import AgentRuntime
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from src.runtimes.capability.registry import CapabilityRegistry
from src.runtimes.capability.runtime import CapabilityRuntime


def make_context(max_iterations: int = 4) -> AgentExecutionContext:
    identity = Identity(user_id="u1", auth_type="api_key", scopes={"*"})
    agent = AgentDefinition(
        name="agent-runtime-test",
        goal="test",
        instruction="Answer the user.",
        tools=["calculator.add"],
    )
    return AgentExecutionContext.create(
        execution_id="exec-runtime-test",
        agent_id=agent.name,
        session_id="session-runtime-test",
        correlation_id="corr-runtime-test",
        identity=identity,
        limits=AgentExecutionLimits(
            max_iterations=max_iterations,
            max_tool_calls=4,
            max_parallel_tools=2,
            timeout_seconds=5,
            inference_timeout_seconds=2,
            tool_timeout_seconds=1,
        ),
        agent=agent,
        input={"prompt": "2 + 3"},
    )


class EmptyContextRuntime:
    class Engine:
        async def load_context(self, session_id, identity):
            class Session:
                messages = []

            class Loaded:
                session = Session()

            return Loaded()

    context_engine = Engine()


class ScriptedInference:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_agent_runtime_single_final_answer():
    inference = ScriptedInference(
        [
            InferenceResponse(
                request_id="i1",
                execution_id="exec-runtime-test",
                iteration=1,
                message=InferenceMessage(role="assistant", content="5"),
                provider="fake",
                model="fake-model",
                usage=InferenceUsage(total_tokens=3),
            )
        ]
    )
    tool_runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
    )
    registry = AgentRegistry()
    registry.register(make_context().agent)
    policy = RegistryAgentToolPolicy(
        registry,
        tool_runtime.registry,
        AuthorizationService(),
    )
    context_builder = ContextBuilderAdapter(
        EmptyContextRuntime(),
        tool_runtime,
        policy,
    )
    tool_port = CapabilityToolExecutionAdapter(
        tool_runtime,
        policy,
        DefaultAgentExecutionPolicy(),
    )
    runtime = AgentRuntime(
        context_builder=context_builder,
        inference=inference,
        tool_execution=tool_port,
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    result = await runtime.execute(make_context())

    assert result.state is AgentLoopState.COMPLETED
    assert result.output == "5"
    assert len(result.iterations) == 1
    assert result.iterations[0].state is AgentLoopState.COMPLETED


@pytest.mark.asyncio
async def test_agent_runtime_executes_tool_then_rebuilds_context_and_completes():
    registry = CapabilityRegistry()
    capability = CapabilityDefinition(
        id="calculator.add",
        name="calculator.add",
        description="Add two integers",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )

    async def add(**kwargs):
        return kwargs["a"] + kwargs["b"]

    registry.register_capability(PythonCapabilityDriver(capability, add))
    capability_runtime = CapabilityRuntime(
        registry=registry,
        authorization=AuthorizationService(),
    )
    agents = AgentRegistry()
    agents.register(make_context().agent)
    tool_policy = RegistryAgentToolPolicy(
        agents,
        registry,
        AuthorizationService(),
    )

    context = make_context()
    responses = [
        InferenceResponse(
            request_id="i1",
            execution_id=context.execution_id,
            iteration=1,
            message=InferenceMessage(
                role="assistant",
                tool_calls=[
                    InferenceToolCall(
                        id="call-1",
                        name="calculator.add",
                        arguments={"a": 2, "b": 3},
                    )
                ],
            ),
            provider="fake",
            model="fake-model",
        ),
        InferenceResponse(
            request_id="i2",
            execution_id=context.execution_id,
            iteration=2,
            message=InferenceMessage(role="assistant", content="5"),
            provider="fake",
            model="fake-model",
        ),
    ]
    inference = ScriptedInference(responses)

    context_builder = ContextBuilderAdapter(
        EmptyContextRuntime(),
        capability_runtime,
        tool_policy,
    )
    tool_port = CapabilityToolExecutionAdapter(
        capability_runtime,
        tool_policy,
        DefaultAgentExecutionPolicy(),
    )
    runtime = AgentRuntime(
        context_builder=context_builder,
        inference=inference,
        tool_execution=tool_port,
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    result = await runtime.execute(context)

    assert result.state is AgentLoopState.COMPLETED
    assert result.output == "5"
    assert len(result.iterations) == 2
    assert result.iterations[0].tool_call_ids == ["call-1"]
    assert inference.requests[1].iteration == 2
    assert any(
        message.role == "tool" and message.tool_call_id == "call-1"
        for message in inference.requests[1].messages
    )


@pytest.mark.asyncio
async def test_agent_runtime_honors_max_iterations_without_agent_recursion():
    context = make_context(max_iterations=2)
    tool_registry = CapabilityRegistry()
    capability_runtime = CapabilityRuntime(
        registry=tool_registry,
        authorization=AuthorizationService(),
    )
    agents = AgentRegistry()
    agents.register(context.agent)
    policy = RegistryAgentToolPolicy(
        agents,
        tool_registry,
        AuthorizationService(),
    )
    context_builder = ContextBuilderAdapter(
        EmptyContextRuntime(),
        capability_runtime,
        policy,
    )
    inference = ScriptedInference(
        [
            InferenceResponse(
                request_id="i1",
                execution_id=context.execution_id,
                iteration=1,
                message=InferenceMessage(
                    role="assistant",
                    tool_calls=[
                        InferenceToolCall(
                            id="call-1",
                            name="calculator.add",
                            arguments={"a": 1, "b": 2},
                        )
                    ],
                ),
                provider="fake",
                model="fake",
            ),
            InferenceResponse(
                request_id="i2",
                execution_id=context.execution_id,
                iteration=2,
                message=InferenceMessage(
                    role="assistant",
                    tool_calls=[
                        InferenceToolCall(
                            id="call-2",
                            name="calculator.add",
                            arguments={"a": 3, "b": 4},
                        )
                    ],
                ),
                provider="fake",
                model="fake",
            ),
        ]
    )

    tool_port = CapabilityToolExecutionAdapter(
        capability_runtime,
        policy,
        DefaultAgentExecutionPolicy(),
    )
    runtime = AgentRuntime(
        context_builder=context_builder,
        inference=inference,
        tool_execution=tool_port,
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    result = await runtime.execute(context)

    assert result.state is AgentLoopState.FAILED
    assert result.error_code in {"CAPABILITY_NOT_FOUND", "MAX_ITERATIONS_EXCEEDED"}
    assert len(result.iterations) == 2
    assert [item.iteration for item in result.iterations] == [1, 2]
    assert len(inference.requests) == 2
    assert inference.requests[0].iteration == 1
    assert inference.requests[1].iteration == 2
    assert result.error_code == "MAX_ITERATIONS_EXCEEDED"