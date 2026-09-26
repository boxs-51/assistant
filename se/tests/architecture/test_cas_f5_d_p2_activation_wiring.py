from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.application.assets.hydration import HydrationResult, HydrationStatus
from se.src.application.assets.projection import CanonicalAssetProviderProjectionHook
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.message import GatewayMessage
from se.src.domain.schemas.response import GatewayChoice, GatewayResponse
from se.src.provider.asset_projection import TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.adapters.policy import DefaultAgentExecutionPolicy
from se.src.runtimes.agent.contracts import AgentContextSnapshot, InferenceMessage
from se.src.runtimes.agent.runtime import AgentRuntime
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.chat.direct import DirectChatRuntime
from se.src.runtimes.provider.runtime import ProviderRuntime
from se.src.runtimes.workflow.runtime import WorkflowRuntime


class _RecordingBus:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, event) -> None:
        self.published.append(event)


class _Storage:
    def __init__(self, object_store) -> None:
        self.object_store = object_store
        self.lookups = []

    def is_driver_available(self, name: str) -> bool:
        return name == "object-local"

    def get_object_storage_driver(self, name: str):
        self.lookups.append(name)
        return self.object_store


def _context(*, storage, uow_factory, http_client):
    return SimpleNamespace(
        storage=storage,
        uow_factory=uow_factory,
        http_client=http_client,
        config=SimpleNamespace(
            assets=SimpleNamespace(storage_driver="object-local"),
            provider=SimpleNamespace(timeout=17.0),
        ),
    )


def _asset_message(asset_id: str = "asset-a") -> list[dict]:
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "file",
                    "data": {
                        "attachment": {
                            "asset_id": asset_id,
                            "source": "asset",
                            "uri": f"asset://{asset_id}",
                            "filename": "doc.pdf",
                            "mime_type": "application/pdf",
                        }
                    },
                }
            ],
        }
    ]


def _identity() -> Identity:
    return Identity(
        user_id="user-a",
        organization_id=None,
        auth_type="guest",
    )


def test_p2_provider_hook_uses_exact_server_owned_dependencies():
    registry = object()
    object_store = object()
    http_client = object()
    uow_factory = lambda: object()
    storage = _Storage(object_store)

    runtime = ProviderRuntime(circuit_breaker_manager=None)
    runtime.provider_registry = registry
    runtime._http_client = http_client

    hook = runtime._build_asset_projection_hook(
        _context(
            storage=storage,
            uow_factory=uow_factory,
            http_client=http_client,
        )
    )

    assert hook is not None
    hydration = hook.hydration_service
    assert hydration.provider_registry is registry
    assert hydration.object_store is object_store
    assert hydration.uow_factory is uow_factory
    assert hydration.http_client is http_client
    assert hydration.timeout == 17.0
    assert storage.lookups == ["object-local"]


def test_p2_provider_hook_unavailable_storage_remains_fail_closed():
    runtime = ProviderRuntime(circuit_breaker_manager=None)
    runtime.provider_registry = object()
    runtime._http_client = object()
    context = _context(
        storage=SimpleNamespace(
            is_driver_available=lambda _name: False,
            get_object_storage_driver=lambda _name: object(),
        ),
        uow_factory=lambda: object(),
        http_client=runtime._http_client,
    )

    assert runtime._build_asset_projection_hook(context) is None
    assert runtime.asset_projection_ready is False


@pytest.mark.asyncio
async def test_p2_provider_readiness_requires_exact_hook_injection():
    runtime = ProviderRuntime(circuit_breaker_manager=None)
    hook = object()
    runtime._asset_projection_hook = hook
    runtime._asset_projection_configured = True
    runtime.chat_handler = SimpleNamespace(asset_projection_hook=hook)

    assert runtime.asset_projection_ready is False
    await runtime.start()
    assert runtime.asset_projection_ready is True

    runtime.chat_handler.asset_projection_hook = object()
    assert runtime.asset_projection_ready is False

    await runtime.stop()
    assert runtime.asset_projection_ready is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DIRECT", "AGENT"])
async def test_p2_workflow_releases_ready_authenticated_direct_agent_assets(mode):
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        provider_runtime=SimpleNamespace(asset_projection_ready=True),
        direct_chat_runtime=object(),
        agent_runtime=object(),
    )
    calls = {"direct": 0, "agent": 0}

    async def direct(*args, **kwargs):
        calls["direct"] += 1

    async def agent(*args, **kwargs):
        calls["agent"] += 1

    runtime._execute_direct = direct
    runtime._execute_agent = agent

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-a",
            turn_id="turn-a",
            payload={
                "identity": _identity(),
                "request_body": {
                    "_chat_execution_mode": mode,
                    "messages": _asset_message(),
                },
            },
        )
    )

    assert calls[mode.lower()] == 1
    assert runtime.event_bus.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "ready", "identity"),
    [
        ("DIRECT", False, _identity()),
        ("AGENT", False, _identity()),
        ("DIRECT", True, None),
        ("AGENT", True, None),
        ("LEGACY", True, _identity()),
    ],
)
async def test_p2_workflow_assets_fail_closed_without_full_release_gate(
    mode,
    ready,
    identity,
):
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        provider_runtime=SimpleNamespace(asset_projection_ready=ready),
        direct_chat_runtime=object(),
        agent_runtime=object(),
    )
    calls = {"direct": 0, "agent": 0}

    async def direct(*args, **kwargs):
        calls["direct"] += 1

    async def agent(*args, **kwargs):
        calls["agent"] += 1

    runtime._execute_direct = direct
    runtime._execute_agent = agent
    payload = {
        "request_body": {
            "_chat_execution_mode": mode,
            "messages": _asset_message(),
        }
    }
    if identity is not None:
        payload["identity"] = identity

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-a",
            turn_id="turn-a",
            payload=payload,
        )
    )

    assert calls == {"direct": 0, "agent": 0}
    assert [event.event_name for event in runtime.event_bus.published] == [
        "provider.failed"
    ]
    failure = runtime.event_bus.published[0]
    assert failure.payload["error_code"] == "ASSET_HYDRATION_REQUIRED"
    assert failure.payload["failure_domain"] == "MESSAGE_ASSET"
    assert failure.payload["status_code"] == 409
    assert failure.payload["retryable"] is False


class _CompositionProvider:
    name = "gemini"

    def __init__(self) -> None:
        self.config = SimpleNamespace(file_binding_namespace="gemini-scope")
        self.probes = 0

    async def has_capability(self, model, capability, http_client, timeout):
        self.probes += 1
        return True


class _CompositionRegistry:
    def __init__(self, provider) -> None:
        self.provider = provider

    def get_provider(self, name):
        return self.provider if name == self.provider.name else None


class _CompositionHydration:
    def __init__(self, provider) -> None:
        self.provider_registry = _CompositionRegistry(provider)
        self.calls = []

    async def hydrate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return HydrationResult(
            status=HydrationStatus.REUSED,
            provider_file_id="files/p2",
            provider_uri="https://provider.invalid/files/p2",
            mime_type="application/pdf",
        )


class _CompositionRouting:
    def __init__(self, provider) -> None:
        self.provider = provider

    def get_fallback_chain(self, model=None, metadata=None):
        return [self.provider]


class _CompositionExecutor:
    def __init__(self) -> None:
        self.retry_policy = SimpleNamespace(max_retries=0)
        self.calls = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, body, **kwargs):
        self.calls.append(
            {
                "provider": provider.name,
                "body": deepcopy(body),
                "call_budget": kwargs.get("call_budget"),
            }
        )
        return GatewayResponse(
            id="resp-p2",
            model="logical-model",
            choices=[
                GatewayChoice(
                    index=0,
                    message=GatewayMessage(
                        role="assistant",
                        content="done",
                    ),
                )
            ],
            metadata={"provider": provider.name},
        )

    async def execute_stream(self, *, provider, body, **kwargs):
        self.calls.append(
            {
                "provider": provider.name,
                "body": deepcopy(body),
                "call_budget": kwargs.get("call_budget"),
            }
        )
        raise AssertionError(
            "legacy canonical asset event must fail before raw stream execution"
        )
        yield  # pragma: no cover


class _AgentContextBuilder:
    async def build(self, context, request):
        return AgentContextSnapshot(
            execution_id=context.execution_id,
            iteration=request.iteration,
            messages=(
                InferenceMessage(
                    role="user",
                    content=context.input.get("prompt"),
                ),
            ),
        )


class _NoTools:
    async def execute_many(self, context, requests, *, max_parallel):
        return []


def _composition_runtime():
    provider = _CompositionProvider()
    hydration = _CompositionHydration(provider)
    hook = CanonicalAssetProviderProjectionHook(hydration)
    executor = _CompositionExecutor()
    handler = ChatExecutionHandler(
        providers={provider.name: provider},
        routing_policy=_CompositionRouting(provider),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        timeout=30.0,
        asset_projection_hook=hook,
    )
    runtime = ProviderRuntime(circuit_breaker_manager=None)
    runtime._http_client = object()
    runtime.chat_handler = handler
    runtime._asset_projection_hook = hook
    runtime._asset_projection_configured = True
    runtime._asset_projection_ready = True
    return runtime, hydration, executor


def _assert_transient_projection(executor, *, expected_owner, hydration):
    assert len(executor.calls) == 1
    body = executor.calls[0]["body"]
    attachment = body["messages"][-1]["content"][0]["data"]["attachment"]
    transient = attachment[TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY]
    assert transient.provider_name == "gemini"
    assert transient.provider_namespace == "gemini-scope"
    assert transient.provider_uri == "https://provider.invalid/files/p2"
    assert hydration.calls == [
        {
            "owner_user_id": expected_owner,
            "asset_id": "asset-a",
            "provider_name": "gemini",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_p2_direct_composes_workflow_runtime_adapter_handler_projection(stream):
    provider_runtime, hydration, executor = _composition_runtime()
    adapter = ProviderInferenceAdapter(
        provider_runtime,
        provider_runtime._http_client,
    )
    direct_runtime = DirectChatRuntime(
        inference=adapter,
        capability_runtime=CapabilityRuntime(
            registry=CapabilityRegistry(),
            authorization=AuthorizationService(),
        ),
    )
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        provider_runtime=provider_runtime,
        direct_chat_runtime=direct_runtime,
        agent_runtime=None,
    )
    canonical = _asset_message()
    original = deepcopy(canonical)

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-direct-p2",
            turn_id="turn-direct-p2",
            payload={
                "identity": _identity(),
                "request_body": {
                    "_chat_execution_mode": "DIRECT",
                    "model": "logical-model",
                    "messages": canonical,
                    "metadata": {},
                    "config": {"stream": stream},
                },
            },
        )
    )

    _assert_transient_projection(
        executor,
        expected_owner="user-a",
        hydration=hydration,
    )
    assert canonical == original
    event_names = [item.event_name for item in runtime.event_bus.published]
    assert event_names == (
        ["provider.stream.chunk_emitted", "provider.stream.completed"]
        if stream
        else ["provider.chat.responded"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_p2_agent_composes_real_agent_inference_adapter_handler_projection(stream):
    provider_runtime, hydration, executor = _composition_runtime()
    adapter = ProviderInferenceAdapter(
        provider_runtime,
        provider_runtime._http_client,
    )
    agent_runtime = AgentRuntime(
        context_builder=_AgentContextBuilder(),
        inference=adapter,
        tool_execution=_NoTools(),
        execution_policy=DefaultAgentExecutionPolicy(),
    )
    agents = AgentRegistry()
    agent = AgentDefinition(
        name="agent-p2",
        goal="prove CAS activation composition",
        instruction="Return a final answer.",
        tools=[],
    )
    agents.register(agent)

    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        provider_runtime=provider_runtime,
        direct_chat_runtime=None,
        agent_runtime=agent_runtime,
        agent_registry=agents,
        capability_runtime=SimpleNamespace(catalog=None),
        authorization_service=AuthorizationService(),
        agent_execution_supervisor=None,
    )
    canonical = _asset_message()
    original = deepcopy(canonical)

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-agent-p2",
            turn_id="turn-agent-p2",
            payload={
                "identity": _identity(),
                "request_body": {
                    "_chat_execution_mode": "AGENT",
                    "agent_id": "agent-p2",
                    "model": "logical-model",
                    "messages": canonical,
                    "metadata": {},
                    "config": {"stream": stream},
                },
            },
        )
    )

    _assert_transient_projection(
        executor,
        expected_owner="user-a",
        hydration=hydration,
    )
    assert canonical == original
    event_names = [item.event_name for item in runtime.event_bus.published]
    assert event_names == (
        ["provider.stream.chunk_emitted", "provider.stream.completed"]
        if stream
        else ["provider.chat.responded"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_p2_actual_legacy_provider_event_stays_closed_with_installed_hook(stream):
    provider_runtime, hydration, executor = _composition_runtime()
    provider_runtime.event_bus = _RecordingBus()

    body = {
        "model": "logical-model",
        "messages": _asset_message(),
        "metadata": {},
        "config": {"stream": stream},
    }
    await provider_runtime._handle_execute_chat(
        BaseEvent(
            event_name="provider.chat.execute",
            session_id="session-legacy-p2",
            turn_id="turn-legacy-p2",
            payload={"request_body": body},
        )
    )

    assert executor.calls == []
    assert hydration.calls == []
    assert [item.event_name for item in provider_runtime.event_bus.published] == [
        "provider.failed"
    ]
    failure = provider_runtime.event_bus.published[0]
    assert "trusted owner" in failure.payload["error"].lower()
