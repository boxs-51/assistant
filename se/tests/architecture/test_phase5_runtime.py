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
async def test_agent_runtime_preserves_canonical_error_from_malformed_tool_arguments():
    context = make_context()

    class ParseFailingInference:
        async def complete(self, request):
            from src.runtimes.agent.tool_execution.errors import ToolArgumentParseError

            raise ToolArgumentParseError(
                "Tool call arguments must contain valid JSON."
            )

    runtime = AgentRuntime(
        context_builder=ContextBuilderAdapter(
            EmptyContextRuntime(),
            CapabilityRuntime(
                registry=CapabilityRegistry(),
                authorization=AuthorizationService(),
            ),
            RegistryAgentToolPolicy(
                AgentRegistry(),
                CapabilityRegistry(),
                AuthorizationService(),
            ),
        ),
        inference=ParseFailingInference(),
        tool_execution=ScriptedToolPort([]),
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    result = await runtime.execute(context)

    assert result.state is AgentLoopState.FAILED
    assert result.error_code == "CAPABILITY_INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_agent_runtime_surfaces_malformed_tool_arguments_as_canonical_error():
    context = make_context()

    class ParseFailingInference:
        async def complete(self, request):
            from src.runtimes.agent.adapters.inference import ToolArgumentParseError

            raise ToolArgumentParseError(
                "Tool call arguments must contain valid JSON."
            )

    runtime = AgentRuntime(
        context_builder=ContextBuilderAdapter(
            EmptyContextRuntime(),
            CapabilityRuntime(
                registry=CapabilityRegistry(),
                authorization=AuthorizationService(),
            ),
            RegistryAgentToolPolicy(
                AgentRegistry(),
                CapabilityRegistry(),
                AuthorizationService(),
            ),
        ),
        inference=ParseFailingInference(),
        tool_execution=ScriptedToolPort([]),
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    result = await runtime.execute(context)

    assert result.state is AgentLoopState.FAILED
    assert result.error_code == "CAPABILITY_INVALID_ARGUMENT"


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


class DelayedContextRuntime:
    def __init__(self, started: asyncio.Event, delay: float):
        self.started = started
        self.delay = delay

    class Engine:
        def __init__(self, owner):
            self.owner = owner

        async def load_context(self, session_id, identity):
            self.owner.started.set()
            await asyncio.sleep(self.owner.delay)
            class Session:
                messages = []
            class Loaded:
                session = Session()
            return Loaded()

    @property
    def context_engine(self):
        return self.Engine(self)


class ScriptedToolPort:
    def __init__(self, results):
        self.results = results
        self.calls = 0

    async def execute_many(self, context, requests, *, max_parallel):
        self.calls += 1
        await asyncio.sleep(0.05)
        return list(reversed(self.results))


@pytest.mark.asyncio
async def test_agent_runtime_preserves_initial_context_across_tool_iterations():
    session_messages = [
        # Minimal fake gateway messages are converted by ContextBuilderAdapter.
    ]

    class HistoryRuntime:
        class Engine:
            async def load_context(self, session_id, identity):
                class Session:
                    messages = []
                class Loaded:
                    session = Session()
                return Loaded()
        context_engine = Engine()

    context = make_context()
    tool_registry = CapabilityRegistry()
    cap_runtime = CapabilityRuntime(
        registry=tool_registry,
        authorization=AuthorizationService(),
    )
    agents = AgentRegistry()
    agents.register(context.agent)
    policy = RegistryAgentToolPolicy(
        agents, tool_registry, AuthorizationService()
    )
    builder = ContextBuilderAdapter(HistoryRuntime(), cap_runtime, policy)

    tool_result = InferenceMessage(
        role="assistant",
        tool_calls=[
            InferenceToolCall(
                id="call-1",
                name="calculator.add",
                arguments={"a": 2, "b": 3},
            )
        ],
    )
    final = InferenceMessage(role="assistant", content="done")

    class CapturingInference:
        def __init__(self):
            self.requests = []
            self.responses = [tool_result, final]

        async def complete(self, request):
            self.requests.append(request)
            return InferenceResponse(
                request_id=f"i{len(self.requests)}",
                execution_id=context.execution_id,
                iteration=request.iteration,
                message=self.responses.pop(0),
                provider="fake",
                model="fake",
            )

    inference = CapturingInference()
    tool_port = ScriptedToolPort([
        __import__("src.runtimes.agent.contracts", fromlist=["ToolExecutionResult"]).ToolExecutionResult(
            execution_id=context.execution_id,
            iteration=1,
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="calculator.add",
            success=True,
            output=5,
        )
    ])
    runtime = AgentRuntime(
        context_builder=builder,
        inference=inference,
        tool_execution=tool_port,
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)

    assert result.state is AgentLoopState.COMPLETED
    assert len(inference.requests) == 2
    assert inference.requests[1].messages[0].role == "system"
    assert any(
        message.role == "tool" and message.tool_call_id == "call-1"
        for message in inference.requests[1].messages
    )


@pytest.mark.asyncio
async def test_agent_runtime_times_out_while_context_builder_is_running():
    started = asyncio.Event()

    class RuntimeWithTimeout:
        class Engine:
            async def load_context(self, session_id, identity):
                started.set()
                await asyncio.sleep(10)
        context_engine = Engine()

    context = make_context()
    context.limits.timeout_seconds = 0.05
    builder = ContextBuilderAdapter(
        RuntimeWithTimeout(),
        CapabilityRuntime(
            registry=CapabilityRegistry(),
            authorization=AuthorizationService(),
        ),
        RegistryAgentToolPolicy(
            AgentRegistry(),
            CapabilityRegistry(),
            AuthorizationService(),
        ),
    )
    inference = ScriptedInference([])

    runtime = AgentRuntime(
        context_builder=builder,
        inference=inference,
        tool_execution=ScriptedToolPort([]),
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    result = await runtime.execute(context)

    assert started.is_set()
    assert result.state is AgentLoopState.TIMEOUT
    assert result.error_code == "AGENT_TIMEOUT"


@pytest.mark.asyncio
async def test_agent_runtime_cancels_context_builder():
    started = asyncio.Event()

    class RuntimeWithCancellation:
        class Engine:
            async def load_context(self, session_id, identity):
                started.set()
                await asyncio.sleep(10)
        context_engine = Engine()

    context = make_context()
    builder = ContextBuilderAdapter(
        RuntimeWithCancellation(),
        CapabilityRuntime(
            registry=CapabilityRegistry(),
            authorization=AuthorizationService(),
        ),
        RegistryAgentToolPolicy(
            AgentRegistry(),
            CapabilityRegistry(),
            AuthorizationService(),
        ),
    )
    runtime = AgentRuntime(
        context_builder=builder,
        inference=ScriptedInference([]),
        tool_execution=ScriptedToolPort([]),
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    task = asyncio.create_task(runtime.execute(context))
    await started.wait()
    context.cancel()

    result = await task
    assert result.state is AgentLoopState.CANCELLED
    assert result.error_code == "AGENT_CANCELLED"


@pytest.mark.asyncio
async def test_agent_runtime_cancellation_interrupts_execute_many():
    context = make_context()
    tool_started = asyncio.Event()

    class BlockingToolPort:
        async def execute_many(self, context, requests, *, max_parallel):
            tool_started.set()
            await asyncio.sleep(10)
            return []

    context_builder = ContextBuilderAdapter(
        EmptyContextRuntime(),
        CapabilityRuntime(
            registry=CapabilityRegistry(),
            authorization=AuthorizationService(),
        ),
        RegistryAgentToolPolicy(
            AgentRegistry(),
            CapabilityRegistry(),
            AuthorizationService(),
        ),
    )

    inference = ScriptedInference([
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
        )
    ])

    runtime = AgentRuntime(
        context_builder=context_builder,
        inference=inference,
        tool_execution=BlockingToolPort(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    task = asyncio.create_task(runtime.execute(context))
    await tool_started.wait()
    context.cancel()

    result = await task
    assert result.state is AgentLoopState.CANCELLED
    assert result.error_code == "AGENT_CANCELLED"


@pytest.mark.asyncio
async def test_agent_runtime_orders_tool_results_by_tool_call_id():
    context = make_context()
    tool_registry = CapabilityRegistry()
    capability_runtime = CapabilityRuntime(
        registry=tool_registry,
        authorization=AuthorizationService(),
    )
    agents = AgentRegistry()
    agents.register(context.agent)
    policy = RegistryAgentToolPolicy(
        agents, tool_registry, AuthorizationService()
    )
    builder = ContextBuilderAdapter(EmptyContextRuntime(), capability_runtime, policy)

    calls = [
        InferenceToolCall(id="call-a", name="calculator.add", arguments={"a": 1, "b": 2}),
        InferenceToolCall(id="call-b", name="calculator.add", arguments={"a": 3, "b": 4}),
    ]

    responses = [
        InferenceResponse(
            request_id="i1",
            execution_id=context.execution_id,
            iteration=1,
            message=InferenceMessage(role="assistant", tool_calls=calls),
            provider="fake",
            model="fake",
        ),
        InferenceResponse(
            request_id="i2",
            execution_id=context.execution_id,
            iteration=2,
            message=InferenceMessage(role="assistant", content="done"),
            provider="fake",
            model="fake",
        ),
    ]
    inference = ScriptedInference(responses)

    class ReorderingToolPort:
        async def execute_many(self, context, requests, *, max_parallel):
            return [
                __import__("src.runtimes.agent.contracts", fromlist=["ToolExecutionResult"]).ToolExecutionResult(
                    execution_id=context.execution_id,
                    iteration=1,
                    invocation_id="inv-b",
                    tool_call_id="call-b",
                    capability_id="calculator.add",
                    success=True,
                    output=7,
                ),
                __import__("src.runtimes.agent.contracts", fromlist=["ToolExecutionResult"]).ToolExecutionResult(
                    execution_id=context.execution_id,
                    iteration=1,
                    invocation_id="inv-a",
                    tool_call_id="call-a",
                    capability_id="calculator.add",
                    success=True,
                    output=3,
                ),
            ]

    runtime = AgentRuntime(
        context_builder=builder,
        inference=inference,
        tool_execution=ReorderingToolPort(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    result = await runtime.execute(context)

    assert result.state is AgentLoopState.COMPLETED
    tool_messages = [
        message for message in inference.requests[1].messages
        if message.role == "tool"
    ]
    assert [message.tool_call_id for message in tool_messages] == ["call-a", "call-b"]


@pytest.mark.asyncio
async def test_agent_runtime_usage_includes_tool_invocations_and_last_results():
    context = make_context()
    registry = CapabilityRegistry()
    capability = CapabilityDefinition(
        id="calculator.add",
        name="calculator.add",
        description="add",
        input_schema={"type": "object"},
    )

    async def add(**kwargs):
        return 5

    registry.register_capability(PythonCapabilityDriver(capability, add))
    capability_runtime = CapabilityRuntime(
        registry=registry,
        authorization=AuthorizationService(),
    )
    agents = AgentRegistry()
    agents.register(context.agent)
    policy = RegistryAgentToolPolicy(agents, registry, AuthorizationService())
    builder = ContextBuilderAdapter(EmptyContextRuntime(), capability_runtime, policy)
    inference = ScriptedInference([
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
            usage=InferenceUsage(total_tokens=3),
        ),
        InferenceResponse(
            request_id="i2",
            execution_id=context.execution_id,
            iteration=2,
            message=InferenceMessage(role="assistant", content="5"),
            provider="fake",
            model="fake",
            usage=InferenceUsage(total_tokens=4),
        ),
    ])
    tool_port = CapabilityToolExecutionAdapter(
        capability_runtime,
        policy,
        DefaultAgentExecutionPolicy(),
    )
    runtime = AgentRuntime(
        context_builder=builder,
        inference=inference,
        tool_execution=tool_port,
        execution_policy=DefaultAgentExecutionPolicy(),
    )

    result = await runtime.execute(context)

    assert result.state is AgentLoopState.COMPLETED
    assert result.usage.total_tokens == 7
    assert result.usage.tool_invocations == 1
    assert len(result.last_tool_results) == 1
    assert result.last_tool_results[0].tool_call_id == "call-1"