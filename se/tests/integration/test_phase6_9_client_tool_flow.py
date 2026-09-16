import asyncio

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.src.runtimes.connection.realtime import RealtimeMultiplexer


class FakeSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def test_client_capability_invocation_uses_bound_connection_and_correlates_result():
    async def scenario():
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
        connections.register(
            "sess-1",
            "user-1",
            socket,
            connection_id="conn-1",
        )
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
                connection_id="conn-1",
            )
        )
        while not socket.messages:
            await asyncio.sleep(0)

        invoke = socket.messages[0]
        assert invoke["type"] == "capability.invoke"
        assert invoke["connection_id"] == "conn-1"
        assert invoke["payload"]["capability_id"] == "desktop.echo"
        from se.src.runtimes.connection.protocol import RealtimeEnvelope
        accepted = await realtime.handle_inbound(
            "conn-1",
            RealtimeEnvelope(
                type="capability.result",
                message_id="result-1",
                session_id="sess-1",
                connection_id="conn-1",
                execution_id="exec-1",
                invocation_id=invoke["invocation_id"],
                payload={"value": "ok"},
            ),
        )
        assert accepted is True
        result = await task
        assert result.output == {"value": "ok"}
        assert result.invocation_id == invoke["invocation_id"]

    asyncio.run(scenario())