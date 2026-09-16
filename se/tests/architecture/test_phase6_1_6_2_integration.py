from __future__ import annotations

import pytest

from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
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
from se.src.runtimes.connection.contracts import ConnectionState
from se.src.runtimes.connection.lifecycle import ConnectionLifecycleRegistry


def make_definition() -> CapabilityDefinition:
    return CapabilityDefinition(
        id="filesystem.read",
        name="filesystem.read",
        description="Read a local file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
            },
            "required": ["path"],
        },
    )


def make_client_implementation() -> CapabilityImplementation:
    definition = make_definition()
    return CapabilityImplementation.from_definition(
        definition,
        implementation_id="desktop-01:filesystem.read",
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_type=CapabilityOwnerType.CLIENT,
        owner_id="user-1",
        connection_id="conn-1",
    )


def make_active_connection() -> ConnectionLifecycleRegistry:
    connections = ConnectionLifecycleRegistry(
        stale_after_seconds=10.0,
    )
    connections.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
        now=100.0,
    )
    connections.activate(
        "conn-1",
        now=100.0,
    )
    return connections


def test_phase6_1_6_2_client_route_requires_active_connection() -> None:
    catalog = CapabilityCatalog()
    definition = make_definition()
    catalog.register_definition(definition)

    implementation = make_client_implementation()
    catalog.register_implementation(implementation)
    catalog.transition_implementation(
        implementation.implementation_id,
        CapabilityImplementationState.ENABLED,
    )

    connections = make_active_connection()

    policy = CapabilityRoutingPolicy(
        connection_availability=connections,
    )

    context = CapabilityRequestContext(
        owner_id="user-1",
        connection_id="conn-1",
    )

    selected = policy.select(
        catalog,
        "filesystem.read",
        context=context,
    )

    assert selected.implementation_id == "desktop-01:filesystem.read"
    assert connections.get("conn-1").state == ConnectionState.ACTIVE


def test_phase6_1_6_2_stale_connection_is_not_routable() -> None:
    catalog = CapabilityCatalog()
    definition = make_definition()
    catalog.register_definition(definition)

    implementation = make_client_implementation()
    catalog.register_implementation(implementation)
    catalog.transition_implementation(
        implementation.implementation_id,
        CapabilityImplementationState.ENABLED,
    )

    connections = make_active_connection()
    policy = CapabilityRoutingPolicy(
        connection_availability=connections,
    )

    connections.evict_stale(now=110.0)

    assert connections.get("conn-1").state == ConnectionState.STALE

    with pytest.raises(PermissionError):
        policy.select(
            catalog,
            "filesystem.read",
            context=CapabilityRequestContext(
                owner_id="user-1",
                connection_id="conn-1",
            ),
        )


def test_phase6_1_6_2_disconnect_is_not_routable() -> None:
    catalog = CapabilityCatalog()
    definition = make_definition()
    catalog.register_definition(definition)

    implementation = make_client_implementation()
    catalog.register_implementation(implementation)
    catalog.transition_implementation(
        implementation.implementation_id,
        CapabilityImplementationState.ENABLED,
    )

    connections = make_active_connection()
    policy = CapabilityRoutingPolicy(
        connection_availability=connections,
    )

    connections.disconnect("conn-1")

    assert connections.get("conn-1").state == ConnectionState.DISCONNECTED

    with pytest.raises(PermissionError):
        policy.select(
            catalog,
            "filesystem.read",
            context=CapabilityRequestContext(
                owner_id="user-1",
                connection_id="conn-1",
            ),
        )


def test_phase6_1_6_2_disconnect_does_not_delete_definition() -> None:
    catalog = CapabilityCatalog()
    definition = make_definition()
    catalog.register_definition(definition)

    implementation = make_client_implementation()
    catalog.register_implementation(implementation)

    connections = make_active_connection()
    connections.disconnect("conn-1")

    assert catalog.get_definition("filesystem.read") == definition
    assert catalog.get_implementation(
        "desktop-01:filesystem.read"
    ).connection_id == "conn-1"