from __future__ import annotations

from types import SimpleNamespace

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
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
