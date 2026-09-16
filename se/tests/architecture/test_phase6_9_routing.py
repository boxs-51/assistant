from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
import pytest

from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.policy import (
    CapabilityRequestContext,
    CapabilityRoutingPolicy,
)
from se.src.runtimes.connection.registry import ConnectionRegistry


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


def test_connection_affinity_rejects_server_fallback():
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

    with pytest.raises(PermissionError, match="No authorized routable implementation"):
        CapabilityRoutingPolicy(
            connection_availability=connections
        ).select(
            catalog,
            "echo",
            context=CapabilityRequestContext(
                owner_id="user-1",
                connection_id="conn-local",
            ),
        )


def test_connection_affinity_rejects_foreign_client_when_local_connection_has_no_implementation():
    catalog = CapabilityCatalog()
    definition = CapabilityDefinition(
        id="echo",
        name="echo",
        description="Echo",
        input_schema={"type": "object"},
    )
    catalog.register_definition(definition)

    foreign_client = _impl(definition, "foreign:echo", "conn-foreign")
    catalog.register_implementation(foreign_client)
    catalog.transition_implementation(
        foreign_client.implementation_id,
        CapabilityImplementationState.ENABLED,
    )

    connections = ConnectionRegistry()
    connections.register("sess-local", "user-1", connection_id="conn-local")
    connections.activate("conn-local")
    connections.register("sess-foreign", "user-1", connection_id="conn-foreign")
    connections.activate("conn-foreign")

    with pytest.raises(PermissionError, match="No authorized routable implementation"):
        CapabilityRoutingPolicy(
            connection_availability=connections
        ).select(
            catalog,
            "echo",
            context=CapabilityRequestContext(
                owner_id="user-1",
                connection_id="conn-local",
            ),
        )


def test_connection_affinity_cannot_be_overridden_by_preferred_implementation():
    catalog = CapabilityCatalog()
    definition = CapabilityDefinition(
        id="echo",
        name="echo",
        description="Echo",
        input_schema={"type": "object"},
    )
    catalog.register_definition(definition)

    local = _impl(definition, "local:echo", "conn-local")
    foreign = _impl(definition, "foreign:echo", "conn-foreign")
    for item in (local, foreign):
        catalog.register_implementation(item)
        catalog.transition_implementation(
            item.implementation_id,
            CapabilityImplementationState.ENABLED,
        )

    connections = ConnectionRegistry()
    connections.register("sess-local", "user-1", connection_id="conn-local")
    connections.activate("conn-local")
    connections.register("sess-foreign", "user-1", connection_id="conn-foreign")
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
        preferred_implementation_id="foreign:echo",
    )

    assert selected.implementation_id == "local:echo"