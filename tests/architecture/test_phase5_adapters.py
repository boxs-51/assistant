from __future__ import annotations

import asyncio
import time

import pytest

from src.domain.schemas.agent import AgentDefinition
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.domain.schemas.identity import Identity
from src.domain.schemas.message import GatewayMessage, MessageContentPart
from src.domain.schemas.response import GatewayChoice, GatewayResponse
from src.domain.schemas.tool import FunctionCall, GatewayToolCall
from src.runtimes.agent.adapters.context import ContextBuilderAdapter
from src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from src.runtimes.agent.adapters.policy import (
    DefaultAgentExecutionPolicy,
    RegistryAgentToolPolicy,
)
from src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from src.runtimes.agent.contracts.context import AgentExecutionContext
from src.runtimes.agent.contracts.context_builder import AgentContextRequest
from src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
)
from src.runtimes.agent.contracts import (
    AgentContextSnapshot
)
from src.runtimes.agent.contracts.tool import ToolExecutionRequest
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from src.runtimes.capability.runtime import CapabilityRuntime
from src.runtimes.capability.registry import CapabilityRegistry
from src.application.policy.authorization import AuthorizationService
from src.agent.registry import AgentRegistry
from src.provider.gemini.converters.chats.request import RequestChats


def identity() -> Identity:
    return Identity(user_id="u1", auth_type="api_key", scopes={"*"})


def make_context(
    *,
    tools: list[str] | None = None,
    cancellation_event: asyncio.Event | None = None,
    max_tool_calls: int = 8,
    max_parallel_tools: int = 2,
) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id="exec-v3",
        agent_id="agent-v3",
        session_id="session-v3",
        correlation_id="corr-v3",
        identity=identity(),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=max_tool_calls,
            max_parallel_tools=max_parallel_tools,
            timeout_seconds=5,
            tool_timeout_seconds=1,
        ),
        input={"prompt": "hello"},
        agent=AgentDefinition(
            name="agent-v3",
            goal="test",
            instruction="You are a test agent.",
            tools=tools or [],
        ),
        now_monotonic=time.monotonic(),
    )


class StubContextEngine:
    async def load_context(self, session_id, identity):
        class Session:
            messages = [
                GatewayMessage(
                    role="user",
                    content=[
                        MessageContentPart(
                            type="text",
                            text="look at this",
                        )
                    ],
                ),
                GatewayMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        GatewayToolCall(
                            id="call-1",
                            function=FunctionCall(
                                name="vision.inspect",
                                arguments='{"x": 1}',
                            ),
                        )
                    ],
                ),
            ]

        class Loaded:
            session = Session()

        return Loaded()


class StubContextRuntime:
    context_engine = StubContextEngine()


def test_context_snapshot_preserves_multimodal_and_tool_call_shape():
    registry = CapabilityRegistry()
    agent_registry = AgentRegistry()
    authorization = AuthorizationService()
    capability = CapabilityDefinition(
        id="vision.inspect",
        name="vision.inspect",
        description="Inspect image",
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
    )

    async def inspect(**kwargs):
        return {"ok": True}

    registry.register_capability(PythonCapabilityDriver(capability, inspect))
    agent_registry.register(
        AgentDefinition(
            name="agent-v3",
            goal="test",
            instruction="You are a test agent.",
            tools=["vision.inspect"],
        )
    )
    policy = RegistryAgentToolPolicy(agent_registry, registry, authorization)
    adapter = ContextBuilderAdapter(
        StubContextRuntime(),
        CapabilityRuntime(registry=registry, authorization=authorization),
        policy,
    )

    snapshot = asyncio.run(
        adapter.build(
            make_context(tools=["vision.inspect"]),
            AgentContextRequest(execution_id="exec-v3", iteration=1),
        )
    )
    assert snapshot.messages[0].role == "system"
    assert snapshot.messages[1].role == "user"
    assert isinstance(snapshot.messages[1].content, tuple)
    assert snapshot.messages[2].tool_calls[0].name == "vision.inspect"
    assert snapshot.tools[0].name == "vision.inspect"


def test_provider_adapter_serialization_is_gemini_compatible_with_tools():
    request = InferenceRequest(
        request_id="req-v3",
        execution_id="exec-v3",
        iteration=2,
        messages=[
            InferenceMessage(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "vision.inspect",
                        "arguments": {"x": 1},
                    }
                ],
            ),
            InferenceMessage(
                role="tool",
                name="vision.inspect",
                tool_call_id="call-1",
                content={"ok": True},
            ),
        ],
        tools=[],
    )
    body = ProviderInferenceAdapter.serialize_request(
        request.model_copy(
            update={
                "tools": [
                    {
                        "name": "vision.inspect",
                        "description": "Inspect image",
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"x": {"type": "INTEGER"}},
                        },
                    }
                ]
            }
        )
    )

    assert body["tools"][0]["name"] == "vision.inspect"
    assert "function" not in body["tools"][0]

    gemini_body = RequestChats().adapt_chat(body)
    contents = gemini_body["contents"]
    assert contents[0]["role"] == "model"
    assert contents[0]["parts"][0]["functionCall"]["name"] == "vision.inspect"
    assert contents[1]["parts"][0]["functionResponse"]["name"] == "vision.inspect"
    assert gemini_body["tools"][0]["function_declarations"][0]["name"] == "vision.inspect"


@pytest.mark.asyncio
async def test_provider_adapter_propagates_timeout_and_cancellation():
    class SlowHandler:
        async def execute_with_fallback(self, http_client, body):
            await asyncio.sleep(10)
            raise AssertionError("provider should have been cancelled before completion")

    class Runtime:
        chat_handler = SlowHandler()

    timeout_request = InferenceRequest(
        request_id="req-timeout",
        execution_id="exec-v3",
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        timeout_seconds=0.01,
    )
    adapter = ProviderInferenceAdapter(Runtime(), object())

    with pytest.raises(asyncio.TimeoutError):
        await adapter.complete(timeout_request)

    cancelled = asyncio.Event()
    cancellation_request = InferenceRequest(
        request_id="req-cancel",
        execution_id="exec-v3",
        iteration=1,
        messages=[{"role": "user", "content": "hello"}],
        cancellation_event=cancelled,
        timeout_seconds=2,
    )

    task = asyncio.create_task(adapter.complete(cancellation_request))
    await asyncio.sleep(0)
    cancelled.set()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_tool_adapter_validates_and_denies_before_capability_execution():
    calls = 0

    async def execute(**kwargs):
        nonlocal calls
        calls = 1
        return 42

    registry = CapabilityRegistry()
    definition = CapabilityDefinition(
        id="calculator.add",
        name="calculator.add",
        description="add",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    runtime = CapabilityRuntime(
        registry=registry,
        authorization=AuthorizationService(),
    )
    registry.register_capability(PythonCapabilityDriver(definition, execute))
    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            name="agent-v3",
            goal="test",
            instruction="test",
            tools=["calculator.add"],
        )
    )
    tool_policy = RegistryAgentToolPolicy(
        agents, registry, AuthorizationService()
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime,
        tool_policy,
        DefaultAgentExecutionPolicy(),
    )
    result = await adapter.execute(
        make_context(tools=["calculator.add"]),
        ToolExecutionRequest(
            execution_id="exec-v3",
            iteration=1,
            invocation_id="inv-1",
            tool_call_id="call-1",
            capability_id="calculator.add",
            arguments={"a": 1},
        ),
    )
    assert result.success is False
    assert result.error_code == "CAPABILITY_INVALID_ARGUMENT"
    assert calls == 0


@pytest.mark.asyncio
async def test_tool_adapter_distinguishes_not_found_from_not_visible():
    registry = CapabilityRegistry()
    runtime = CapabilityRuntime(
        registry=registry,
        authorization=AuthorizationService(),
    )
    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            name="agent-v3",
            goal="test",
            instruction="test",
            tools=[],
        )
    )
    policy = RegistryAgentToolPolicy(
        agents, registry, AuthorizationService()
    )
    adapter = CapabilityToolExecutionAdapter(
        runtime, policy, DefaultAgentExecutionPolicy()
    )
    context = make_context(tools=[])

    missing = await adapter.execute(
        context,
        ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=1,
            invocation_id="inv-missing",
            tool_call_id="call-missing",
            capability_id="missing.tool",
            arguments={},
        ),
    )
    assert missing.error_code == "CAPABILITY_NOT_FOUND"

    async def hidden(**kwargs):
        return "never"

    registry.register_capability(
        PythonCapabilityDriver(
            CapabilityDefinition(
                id="hidden.tool",
                name="hidden.tool",
                description="hidden",
                input_schema={"type": "object"},
            ),
            hidden,
        )
    )

    hidden_result = await adapter.execute(
        context,
        ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=1,
            invocation_id="inv-hidden",
            tool_call_id="call-hidden",
            capability_id="hidden.tool",
            arguments={},
        ),
    )
    assert hidden_result.error_code == "AGENT_TOOL_NOT_VISIBLE"


@pytest.mark.asyncio
async def test_tool_adapter_normalizes_generic_driver_failure():
    registry = CapabilityRegistry()

    async def fail(**kwargs):
        raise RuntimeError("internal failure")

    registry.register_capability(
        PythonCapabilityDriver(
            CapabilityDefinition(
                id="generic.failure",
                name="generic.failure",
                description="failure",
                input_schema={"type": "object"},
            ),
            fail,
        )
    )
    capability_runtime = CapabilityRuntime(
        registry=registry,
        authorization=AuthorizationService(),
    )
    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            name="agent-v3",
            goal="test",
            instruction="test",
            tools=["generic.failure"],
        )
    )
    policy = RegistryAgentToolPolicy(
        agents, registry, AuthorizationService()
    )
    adapter = CapabilityToolExecutionAdapter(
        capability_runtime,
        policy,
        DefaultAgentExecutionPolicy(),
    )

    result = await adapter.execute(
        make_context(tools=["generic.failure"]),
        ToolExecutionRequest(
            execution_id="exec-v3",
            iteration=1,
            invocation_id="inv-generic",
            tool_call_id="call-generic",
            capability_id="generic.failure",
            arguments={},
        ),
    )

    assert result.success is False
    assert result.error_code == "CAPABILITY_EXECUTION_FAILED"


def test_capability_registry_rejects_invalid_schema():
    registry = CapabilityRegistry()

    async def noop(**kwargs):
        return None

    definition = CapabilityDefinition(
        id="invalid.schema",
        name="invalid.schema",
        description="invalid",
        input_schema={"type": 123},
    )

    with pytest.raises(Exception):
        registry.register_capability(
            PythonCapabilityDriver(definition, noop)
        )


def test_provider_adapter_rejects_malformed_tool_arguments_with_canonical_code():
    with pytest.raises(Exception) as exc:
        ProviderInferenceAdapter._parse_arguments("{broken-json")
    assert getattr(exc.value, "code", None) == "CAPABILITY_INVALID_ARGUMENT"

    with pytest.raises(Exception) as exc:
        ProviderInferenceAdapter._parse_arguments("[1, 2, 3]")
    assert getattr(exc.value, "code", None) == "CAPABILITY_INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_capability_runtime_shares_agent_cancellation_event():
    event = asyncio.Event()
    registry = CapabilityRegistry()
    definition = CapabilityDefinition(
        id="slow.tool",
        name="slow.tool",
        description="slow",
    )

    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(10)
        return "never"

    registry.register_capability(PythonCapabilityDriver(definition, slow))
    runtime = CapabilityRuntime(
        registry=registry,
        authorization=AuthorizationService(),
    )
    context = make_context(
        tools=["slow.tool"],
        cancellation_event=event,
    )
    task = asyncio.create_task(
        runtime.execute_capability(
            "slow.tool",
            {},
            identity(),
            execution_id=context.execution_id,
            cancellation_event=event,
            timeout_seconds=4,
        )
    )
    await started.wait()
    event.set()
    with pytest.raises(Exception) as exc:
        await task
    assert getattr(exc.value, "code", None) == "CAPABILITY_CANCELLED"


@pytest.mark.asyncio
async def test_tool_parallelism_is_bounded_by_request_and_execution_limits():
    active = 0
    peak = 0
    started = 0
    lock = asyncio.Lock()

    async def tool(**kwargs):
        nonlocal active, peak, started
        async with lock:
            active += 1
            started += 1
            peak = max(peak, active)
        try:
            await asyncio.sleep(0.02)
            return kwargs["value"]
        finally:
            async with lock:
                active -= 1

    registry = CapabilityRegistry()
    for name in ("a", "b", "c", "d"):
        registry.register_capability(
            PythonCapabilityDriver(
                CapabilityDefinition(
                    id=name,
                    name=name,
                    description=name,
                    input_schema={"type": "object"},
                ),
                tool,
            )
        )

    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            name="agent-v3",
            goal="test",
            instruction="test",
            tools=["a", "b", "c", "d"],
        )
    )

    runtime = CapabilityRuntime(
        registry=registry, authorization=AuthorizationService()
    )
    policy = RegistryAgentToolPolicy(agents, registry, AuthorizationService())
    adapter = CapabilityToolExecutionAdapter(
        runtime, policy, DefaultAgentExecutionPolicy()
    )
    context = make_context(
        tools=["a", "b", "c", "d"],
        max_tool_calls=4,
        max_parallel_tools=2,
    )

    requests = [
        ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=1,
            invocation_id=f"inv-{i}",
            tool_call_id=f"call-{i}",
            capability_id=name,
            arguments={"value": name},
        )
        for i, name in enumerate(("a", "b", "c", "d"))
    ]

    results = await adapter.execute_many(context, requests, max_parallel=8)

    assert peak == 1
    assert started == 4
    assert [result.output for result in results] == ["a", "b", "c", "d"]


@pytest.mark.asyncio
async def test_tool_budget_is_reserved_atomically_under_parallel_execution():
    active = 0
    executed = 0
    lock = asyncio.Lock()

    async def tool(**kwargs):
        nonlocal active, executed
        async with lock:
            active += 1
            executed += 1
        try:
            await asyncio.sleep(0.02)
            return kwargs["value"]
        finally:
            async with lock:
                active -= 1

    registry = CapabilityRegistry()
    for name in ("a", "b", "c", "d"):
        registry.register_capability(
            PythonCapabilityDriver(
                CapabilityDefinition(
                    id=name,
                    name=name,
                    description=name,
                    input_schema={"type": "object"},
                ),
                tool,
            )
        )

    agents = AgentRegistry()
    agents.register(
        AgentDefinition(
            name="agent-v3",
            goal="test",
            instruction="test",
            tools=["a", "b", "c", "d"],
        )
    )

    runtime = CapabilityRuntime(
        registry=registry, authorization=AuthorizationService()
    )
    policy = RegistryAgentToolPolicy(agents, registry, AuthorizationService())
    adapter = CapabilityToolExecutionAdapter(
        runtime, policy, DefaultAgentExecutionPolicy()
    )
    context = make_context(
        tools=["a", "b", "c", "d"],
        max_tool_calls=2,
        max_parallel_tools=4,
    )

    requests = [
        ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=1,
            invocation_id=f"inv-{i}",
            tool_call_id=f"call-{i}",
            capability_id=name,
            arguments={"value": name},
        )
        for i, name in enumerate(("a", "b", "c", "d"))
    ]

    results = await adapter.execute_many(context, requests, max_parallel=4)

    assert executed == 2
    assert context.tool_calls_used == 2
    assert sum(result.success for result in results) == 2
    assert sum(
        result.error_code == "AGENT_TOOL_BUDGET_EXCEEDED"
        for result in results
    ) == 2


def test_context_snapshot_nested_models_and_metadata_are_immutable():
    snapshot = AgentContextSnapshot(
        execution_id="exec-v3",
        iteration=1,
        messages=[
            InferenceMessage(
                role="user",
                content=[{"type": "text", "text": "hello"}],
                metadata={"nested": {"value": 1}},
            )
        ],
        tools=[
            {
                "name": "calculator.add",
                "description": "add",
                "parameters": {"type": "object"},
            }
        ],
        metadata={"trace": {"span": "one"}},
    )

    with pytest.raises((TypeError, ValueError)):
        snapshot.iteration = 2

    with pytest.raises((TypeError, ValueError)):
        snapshot.messages[0].role = "assistant"

    with pytest.raises(TypeError):
        snapshot.messages[0].metadata["nested"]["value"] = 2

    with pytest.raises(TypeError):
        snapshot.metadata["trace"]["span"] = "two"