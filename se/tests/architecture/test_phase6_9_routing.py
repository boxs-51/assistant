from src.runtimes.capability.catalog import CapabilityCatalog
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from src.runtimes.capability.policy import (
    CapabilityRequestContext,
    CapabilityRoutingPolicy,
)
from src.runtimes.connection.registry import ConnectionRegistry


def _impl(definition, implementation_id, connection_id):
    return CapabilityImplementation.from_definition(
        definition,
        implementation_id=implementation_id,
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_type=CapabilityOwnerType.CLIENT,
        owner_id="user-1",
        connection_id=connection_id,
    )


def test_active_session_connection_is_preferred_and_other_clients_are_never_selected():
    catalog = CapabilityCatalog()
    definition = CapabilityDefinition(
        id="desktop.echo",
        name="desktop.echo",
        description="Echo",
        input_schema={"type": "object"},
    )
    catalog.register_definition(definition)

    for item in (
        _impl(definition, "client-a:desktop.echo", "conn-a"),
        _impl(definition, "client-b:desktop.echo", "conn-b"),
    ):
        catalog.register_implementation(item)
        catalog.transition_implementation(
            item.implementation_id,
            CapabilityImplementationState.ENABLED,
        )

    connections = ConnectionRegistry()
    connections.register("sess-a", "user-1", connection_id="conn-a")
    connections.activate("conn-a")
    connections.register("sess-b", "user-1", connection_id="conn-b")
    connections.activate("conn-b")

    policy = CapabilityRoutingPolicy(connection_availability=connections)

    selected = policy.select(
        catalog,
        "desktop.echo",
        context=CapabilityRequestContext(
            owner_id="user-1",
            connection_id="conn-a",
        ),
    )

    assert selected.implementation_id == "client-a:desktop.echo"


def test_connection_affinity_allows_server_fallback_but_excludes_other_clients():
    catalog = CapabilityCatalog()
    definition = CapabilityDefinition(
        id="echo",
        name="echo",
        description="Echo",
        input_schema={"type": "object"},
    )
    catalog.register_definition(definition)

    server = CapabilityImplementation.from_definition(
        definition,
        implementation_id="server:echo",
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="SERVER_REGISTRY",
        owner_type=CapabilityOwnerType.SYSTEM,
    )
    foreign_client = _impl(definition, "foreign:echo", "conn-foreign")
    for item in (foreign_client, server):
        catalog.register_implementation(item)
        catalog.transition_implementation(
            item.implementation_id,
            CapabilityImplementationState.ENABLED,
        )

    connections = ConnectionRegistry()
    connections.register("sess-1", "user-1", connection_id="conn-local")
    connections.activate("conn-local")
    connections.register("sess-f", "user-1", connection_id="conn-foreign")
    connections.activate("conn-foreign")

    selected = CapabilityRoutingPolicy(
        connection_availability=connections
    ).select(
        catalog,
        "echo",
        context=CapabilityRequestContext(
            owner_id="user-1",
            connection_id="conn-local",
        ),
    )

    assert selected.implementation_id == "server:echo"