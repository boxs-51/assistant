from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.resolver import (
    AgentResolutionError,
    AgentResolver,
)
from se.src.runtimes.workflow.runtime import WorkflowRuntime


def _event() -> BaseEvent:
    return BaseEvent(
        event_name="context.event.built",
        session_id="session-aos1",
        turn_id="turn-aos1",
        payload={
            "identity": Identity(
                user_id="user-aos1",
                auth_type="guest",
            )
        },
    )


def test_explicit_agent_id_wins_over_compatibility_default() -> None:
    explicit = SimpleNamespace(name="explicit-agent")
    default = SimpleNamespace(name="default-agent")
    agents = {
        "explicit-agent": explicit,
        "default-agent": default,
    }
    resolver = AgentResolver(
        SimpleNamespace(get=lambda agent_id: agents.get(agent_id))
    )

    selection = resolver.select(
        {
            "agent_id": " explicit-agent ",
            "metadata": {
                "routing": {
                    "default_agent_id": "default-agent",
                }
            },
        }
    )
    resolution = resolver.resolve(selection)

    assert selection.agent_id == "explicit-agent"
    assert selection.source == "EXPLICIT"
    assert resolution is not None
    assert resolution.agent is explicit
    assert resolution.source == "EXPLICIT"


def test_default_agent_id_resolves_when_explicit_id_is_absent() -> None:
    default = SimpleNamespace(name="default-agent")
    resolver = AgentResolver(
        SimpleNamespace(
            get=lambda agent_id: default
            if agent_id == "default-agent"
            else None
        )
    )

    selection = resolver.select(
        {
            "metadata": {
                "routing": {
                    "default_agent_id": " default-agent ",
                }
            }
        }
    )
    resolution = resolver.resolve(selection)

    assert selection.agent_id == "default-agent"
    assert selection.source == "DEFAULT"
    assert resolution is not None
    assert resolution.agent is default


def test_resolver_reports_unknown_selected_agent_deterministically() -> None:
    resolver = AgentResolver(SimpleNamespace(get=lambda _agent_id: None))
    selection = resolver.select({"agent_id": "missing-agent"})

    with pytest.raises(AgentResolutionError) as raised:
        resolver.resolve(selection)

    assert raised.value.code == "AGENT_NOT_FOUND"
    assert raised.value.agent_id == "missing-agent"
    assert raised.value.source == "EXPLICIT"


@pytest.mark.asyncio
async def test_explicit_unknown_agent_fails_without_direct_fallback() -> None:
    runtime = WorkflowRuntime()
    runtime.event_bus = SimpleNamespace(publish=AsyncMock())
    runtime.container = SimpleNamespace(
        capability_runtime=None,
        agent_registry=SimpleNamespace(get=lambda _agent_id: None),
    )
    runtime._execute_direct = AsyncMock()

    await runtime._execute_agent(
        _event(),
        {
            "agent_id": "missing-agent",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    runtime._execute_direct.assert_not_awaited()
    failure = runtime.event_bus.publish.await_args.args[0]
    assert failure.event_name == "provider.failed"
    assert failure.payload["error_code"] == "AGENT_NOT_FOUND"
    assert failure.payload["failure_domain"] == "AGENT_RESOLUTION"
    assert failure.payload["retryable"] is False
    assert failure.payload["status_code"] == 404
    assert failure.payload["requested_agent_id"] == "missing-agent"
    assert failure.payload["resolution_source"] == "EXPLICIT"


@pytest.mark.asyncio
async def test_invalid_default_agent_fails_without_direct_fallback() -> None:
    runtime = WorkflowRuntime()
    runtime.event_bus = SimpleNamespace(publish=AsyncMock())
    runtime.container = SimpleNamespace(
        capability_runtime=None,
        agent_registry=SimpleNamespace(get=lambda _agent_id: None),
    )
    runtime._execute_direct = AsyncMock()

    await runtime._execute_agent(
        _event(),
        {
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {
                "routing": {
                    "default_agent_id": "missing-default",
                }
            },
        },
    )

    runtime._execute_direct.assert_not_awaited()
    failure = runtime.event_bus.publish.await_args.args[0]
    assert failure.event_name == "provider.failed"
    assert failure.payload["error_code"] == "AGENT_NOT_FOUND"
    assert failure.payload["requested_agent_id"] == "missing-default"
    assert failure.payload["resolution_source"] == "DEFAULT"


@pytest.mark.asyncio
async def test_no_explicit_or_default_agent_preserves_compatibility_fallback() -> None:
    runtime = WorkflowRuntime()
    runtime.container = SimpleNamespace(
        agent_registry=SimpleNamespace(get=lambda _agent_id: None),
    )
    runtime._execute_direct = AsyncMock()

    await runtime._execute_agent(
        _event(),
        {
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"routing": {}},
        },
    )

    notice = runtime._execute_direct.await_args.kwargs["fallback_notice"]
    assert notice == {
        "status": "AGENT_FALLBACK",
        "reason": "AGENT_NOT_SPECIFIED",
        "message": (
            "No agent_id was specified; chat_direct handled this request."
        ),
        "fallback": "DIRECT",
    }


@pytest.mark.asyncio
async def test_existing_authorization_precedes_lazy_agent_resolution() -> None:
    registry_get = Mock(return_value=SimpleNamespace(name="private-agent"))
    definition = SimpleNamespace(id="private-agent")
    catalog = SimpleNamespace(
        contains_definition=lambda _agent_id: True,
        get_definition=lambda _agent_id: definition,
    )
    authorization = SimpleNamespace(is_allowed=Mock(return_value=False))

    runtime = WorkflowRuntime()
    runtime.event_bus = SimpleNamespace(publish=AsyncMock())
    runtime.container = SimpleNamespace(
        capability_runtime=SimpleNamespace(catalog=catalog),
        authorization_service=authorization,
        agent_registry=SimpleNamespace(get=registry_get),
    )
    runtime._execute_direct = AsyncMock()

    await runtime._execute_agent(
        _event(),
        {
            "agent_id": "private-agent",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    authorization.is_allowed.assert_called_once()
    registry_get.assert_not_called()
    runtime._execute_direct.assert_not_awaited()
    failure = runtime.event_bus.publish.await_args.args[0]
    assert failure.event_name == "provider.failed"
    assert failure.payload["error_code"] == "PermissionError"
    assert failure.payload["status_code"] == 403
