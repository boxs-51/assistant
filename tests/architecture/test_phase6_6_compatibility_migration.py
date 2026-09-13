from __future__ import annotations

import asyncio

from src.application.policy.authorization import AuthorizationService
from src.domain.schemas.identity import Identity
from src.runtimes.capability.catalog import CapabilityCatalog
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from src.runtimes.capability.policy import CapabilityRoutingPolicy
from src.runtimes.capability.runtime import CapabilityRuntime
from src.runtimes.connection.protocol import RealtimeEnvelope
from src.runtimes.connection.registry import ConnectionRegistry
from src.runtimes.connection.realtime import RealtimeMultiplexer


class FakeSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def test_capability_runtime_routes_client_without_agent_location_branch() -> None:
    async def scenario() -> None:
        catalog = CapabilityCatalog()
        definition = CapabilityDefinition(
            id="desktop.echo",
            name="desktop.echo",
            description="Remote echo",
            input_schema={"type": "object"},
        )
        catalog.register_definition(definition)
        implementation = CapabilityImplementation.from_definition(
            definition,
            implementation_id="desktop-01:desktop.echo",
            location=CapabilityExecutionLocation.CLIENT,
            driver_kind="REMOTE_CLIENT",
            owner_type=CapabilityOwnerType.CLIENT,
            owner_id="user-1",
            connection_id="conn-1",
        )
        catalog.register_implementation(implementation)
        catalog.transition_implementation(
            implementation.implementation_id,
            CapabilityImplementationState.ENABLED,
        )

        connections = ConnectionRegistry()
        socket = FakeSocket()
        connections.register("sess-1", "user-1", socket, connection_id="conn-1")
        connections.activate("conn-1")
        realtime = RealtimeMultiplexer(connections)
        runtime = CapabilityRuntime(
            authorization=AuthorizationService(),
            catalog=catalog,
            routing_policy=CapabilityRoutingPolicy(
                connection_availability=connections,
            ),
            connection_registry=connections,
            realtime=realtime,
        )
        identity = Identity(
            user_id="user-1",
            session_id="sess-1",
            auth_type="jwt",
        )

        task = asyncio.create_task(
            runtime.execute_capability(
                "desktop.echo",
                {"value": "ok"},
                identity,
                execution_id="exec-1",
                session_id="sess-1",
                metadata={"connection_id": "conn-1"},
            )
        )
        for _ in range(10):
            if socket.messages:
                break
            await asyncio.sleep(0)
        assert socket.messages[0]["type"] == "capability.invoke"
        assert socket.messages[0]["payload"] == {
            "capability_id": "desktop.echo",
            "arguments": {"value": "ok"},
        }

        assert await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="result-1",
                connection_id="conn-1",
                invocation_id=socket.messages[0]["invocation_id"],
                payload={"value": "ok"},
            ),
        )
        result = await task
        assert result.output == {"value": "ok"}
        assert result.capability_id == "desktop.echo"

    asyncio.run(scenario())