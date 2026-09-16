from __future__ import annotations

import asyncio

import pytest

from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.contracts.registration import (
    CapabilityRegistration,
    ClientCapabilityRegistration,
)
from se.src.runtimes.capability.registration import ClientCapabilityRegistrationService
from se.src.runtimes.connection.protocol import RealtimeEnvelope
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.src.runtimes.connection.realtime import RealtimeMultiplexer
from se.src.runtimes.connection.runtime import ConnectionRuntime


class FakeSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def active_registry() -> tuple[ConnectionRegistry, FakeSocket]:
    registry = ConnectionRegistry()
    socket = FakeSocket()
    registry.register("sess-1", "user-1", socket, connection_id="conn-1")
    registry.activate("conn-1")
    return registry, socket


def test_timeout_and_duplicate_terminal_result_are_resilient() -> None:
    async def scenario() -> None:
        registry, socket = active_registry()
        realtime = RealtimeMultiplexer(registry, default_timeout=0.01)
        invocation = RealtimeEnvelope(
            type="capability.invoke",
            message_id="msg-1",
            connection_id="conn-1",
            invocation_id="inv-1",
            payload={},
        )
        with pytest.raises(asyncio.TimeoutError):
            await realtime.invoke(invocation)
        assert socket.messages[-1]["type"] == "capability.cancel"

        assert await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="late-1",
                connection_id="conn-1",
                invocation_id="inv-1",
                payload={},
            ),
        ) is False

    asyncio.run(scenario())


def test_disconnect_fails_only_affected_pending_invocations() -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        socket_a = FakeSocket()
        socket_b = FakeSocket()
        registry.register("sess-a", "user-1", socket_a, connection_id="a")
        registry.register("sess-b", "user-1", socket_b, connection_id="b")
        registry.activate("a")
        registry.activate("b")
        realtime = RealtimeMultiplexer(registry)
        task_a = asyncio.create_task(realtime.invoke(RealtimeEnvelope(
            type="capability.invoke", message_id="a", connection_id="a",
            invocation_id="inv-a", payload={}
        )))
        task_b = asyncio.create_task(realtime.invoke(RealtimeEnvelope(
            type="capability.invoke", message_id="b", connection_id="b",
            invocation_id="inv-b", payload={}
        )))
        for _ in range(10):
            if len(socket_a.messages) == 1 and len(socket_b.messages) == 1:
                break
            await asyncio.sleep(0)
        assert await realtime.disconnect("a") == 1
        with pytest.raises(ConnectionError):
            await task_a
        assert await realtime.handle_inbound(
            "b",
            RealtimeEnvelope(
                type="capability.result", message_id="b-result",
                connection_id="b", invocation_id="inv-b", payload={"ok": True}
            ),
        )
        assert await task_b == {"ok": True}

    asyncio.run(scenario())


def test_disconnect_cleanup_removes_client_implementation_from_routing() -> None:
    async def scenario() -> None:
        catalog = CapabilityCatalog()
        definition = CapabilityDefinition(
            id="desktop.echo", name="desktop.echo", description="echo",
            input_schema={"type": "object"},
        )
        catalog.register_definition(definition)
        implementation = CapabilityImplementation.from_definition(
            definition,
            implementation_id="desktop-01:desktop.echo",
            location=CapabilityExecutionLocation.CLIENT,
            driver_kind="REMOTE_CLIENT",
            owner_type=CapabilityOwnerType.CLIENT,
            owner_id="user-1", connection_id="conn-1",
        )
        catalog.register_implementation(implementation)
        catalog.transition_implementation(
            implementation.implementation_id,
            CapabilityImplementationState.ENABLED,
        )
        registry, _ = active_registry()
        service = ClientCapabilityRegistrationService(catalog, registry)
        runtime = ConnectionRuntime(registration_service=service)
        runtime.registry = registry
        runtime.realtime = RealtimeMultiplexer(registry)

        await runtime.disconnect_connection("conn-1")
        assert catalog.list_implementations(
            "desktop.echo", routable_only=True
        ) == []

    asyncio.run(scenario())