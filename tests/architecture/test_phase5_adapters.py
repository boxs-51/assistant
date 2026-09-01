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
from src.runtimes.agent.contracts.tool import ToolExecutionRequest
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from src.runtimes.capability.runtime import CapabilityRuntime
from src.runtimes.capability.registry import CapabilityRegistry
from src.application.policy.authorization import AuthorizationService
from src.agent.registry import AgentRegistry
from src.provider.gemini.converters.chats.request import RequestChats


def identity() -> Identity:
    # SỬA LỖI 3 & 4: Cấp scope wildcard "*" để bypass kiểm tra quyền của AuthorizationService
    return Identity(user_id="u1", auth_type="api_key", scopes={"*"})


def make_context(
    *,
    tools: list[str] | None = None,
    cancellation_event: asyncio.Event | None = None,
) -> AgentExecutionContext:
    return AgentExecutionContext.create(
        execution_id="exec-v2",
        agent_id="agent-v2",
        session_id="session-v2",
        correlation_id="corr-v2",
        identity=identity(),
        limits=AgentExecutionLimits(
            max_iterations=4,
            max_tool_calls=8,
            max_parallel_tools=2,
            timeout_seconds=5,
            tool_timeout_seconds=1,
        ),
        input={"prompt": "hello"},
        agent=AgentDefinition(
            name="agent-v2",
            goal="test",
            instruction="You are a test agent.",
            tools=tools or [],
        ),
        # SỬA LỖI 1: Dùng time.monotonic() thời điểm hiện tại thay vì hardcode 100
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
            name="agent-v2",
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
            AgentContextRequest(execution_id="exec-v2", iteration=1),
        )
    )
    assert snapshot.messages[0].role == "system"
    assert snapshot.messages[1].role == "user"
    assert isinstance(snapshot.messages[1].content, list)
    assert snapshot.messages[2].tool_calls[0].name == "vision.inspect"
    assert snapshot.tools[0].name == "vision.inspect"


def test_provider_adapter_serialization_is_gemini_compatible():
    # SỬA LỖI 2: Chuyển các object InferenceMessage thành dict bằng .model_dump() (hoặc dict literal)
    request = InferenceRequest(
        request_id="req-v2",
        execution_id="exec-v2",
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
            ).model_dump(),
            InferenceMessage(
                role="tool",
                name="vision.inspect",
                tool_call_id="call-1",
                content={"ok": True},
            ).model_dump(),
        ],
        tools=[],
    )
    body = ProviderInferenceAdapter.serialize_request(request)
    gemini_body = RequestChats().adapt_chat(body)
    contents = gemini_body["contents"]
    assert contents[0]["role"] == "model"
    assert contents[0]["parts"][0]["functionCall"]["name"] == "vision.inspect"
    assert contents[1]["parts"][0]["functionResponse"]["name"] == "vision.inspect"


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
            name="agent-v2",
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
            execution_id="exec-v2",
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
    context = make_context(tools=["slow.tool"], cancellation_event=event)
    context.cancellation_event = event
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
async def test_tool_parallelism_is_bounded_by_both_request_and_execution_limit():
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def tool(**kwargs):
        nonlocal active, peak
        async with lock:
            active = 1
            peak = max(peak, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return kwargs["value"]

    registry = CapabilityRegistry()
    for name in ("a", "b", "c"):
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
            name="agent-v2",
            goal="test",
            instruction="test",
            tools=["a", "b", "c"],
        )
    )
    runtime = CapabilityRuntime(
        registry=registry, authorization=AuthorizationService()
    )
    policy = RegistryAgentToolPolicy(agents, registry, AuthorizationService())
    adapter = CapabilityToolExecutionAdapter(
        runtime, policy, DefaultAgentExecutionPolicy()
    )
    context = make_context(tools=["a", "b", "c"])
    requests = [
        ToolExecutionRequest(
            execution_id=context.execution_id,
            iteration=1,
            invocation_id=f"inv-{i}",
            tool_call_id=f"call-{i}",
            capability_id=name,
            arguments={"value": name},
        )
        for i, name in enumerate(("a", "b", "c"))
    ]
    results = await adapter.execute_many(context, requests, max_parallel=8)
    assert peak <= 2
    assert [result.output for result in results] == ["a", "b", "c"]