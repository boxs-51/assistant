from __future__ import annotations

import pytest

from src.runtimes.capability.catalog import (
    CapabilityCatalog,
    CapabilityImplementationConflictError,
    InvalidCapabilityBindingError,
    InvalidCapabilityStateTransitionError,
)
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
)
from src.runtimes.capability.policy import (
    CapabilityRequestContext,
    CapabilityRoutingPolicy,
)


def make_definition(
    capability_id: str = "demo.echo",
    *,
    required_scopes: list[str] | None = None,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        name="demo.echo",
        description="Test capability",
        input_schema={
            "type": "object",
            "properties": {},
        },
        required_scopes=required_scopes or [],
    )


def make_server_implementation(
    capability_id: str = "demo.echo",
    implementation_id: str = "server-1",
) -> CapabilityImplementation:
    return CapabilityImplementation.from_definition(
        make_definition(capability_id),
        implementation_id=implementation_id,
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="PYTHON",
    )


def make_client_implementation(
    capability_id: str = "demo.echo",
    implementation_id: str = "client-1",
    owner_id: str = "owner-1",
    connection_id: str = "conn-1",
) -> CapabilityImplementation:
    return CapabilityImplementation.from_definition(
        make_definition(capability_id),
        implementation_id=implementation_id,
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_id=owner_id,
        connection_id=connection_id,
    )


def test_same_capability_can_have_multiple_implementations() -> None:
    catalog = CapabilityCatalog()
    definition = make_definition()

    catalog.register_definition(definition)
    catalog.register_implementation(
        make_server_implementation(implementation_id="server-1")
    )
    catalog.register_implementation(
        make_client_implementation(implementation_id="client-1")
    )

    implementations = catalog.list_implementations("demo.echo")

    assert [item.implementation_id for item in implementations] == [
        "client-1",
        "server-1",
    ]
    assert all(
        item.capability_id == definition.capability_id
        for item in implementations
    )


def test_duplicate_implementation_id_is_rejected() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())
    catalog.register_implementation(
        make_server_implementation(implementation_id="same")
    )

    with pytest.raises(CapabilityImplementationConflictError):
        catalog.register_implementation(
            make_server_implementation(implementation_id="same")
        )


def test_unknown_capability_cannot_receive_implementation() -> None:
    catalog = CapabilityCatalog()

    with pytest.raises(ValueError):
        catalog.register_implementation(
            make_server_implementation("missing", "server-1")
        )


def test_client_implementation_requires_owner_and_connection() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())

    implementation = CapabilityImplementation.from_definition(
        make_definition(),
        implementation_id="client-1",
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
    )

    with pytest.raises(InvalidCapabilityBindingError):
        catalog.register_implementation(implementation)


def test_server_implementation_cannot_bind_connection() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())

    implementation = CapabilityImplementation.from_definition(
        make_definition(),
        implementation_id="server-1",
        location=CapabilityExecutionLocation.SERVER,
        driver_kind="PYTHON",
        connection_id="conn-1",
    )

    with pytest.raises(InvalidCapabilityBindingError):
        catalog.register_implementation(implementation)


def test_implementation_lifecycle_is_enforced() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())
    catalog.register_implementation(
        make_server_implementation(implementation_id="server-1")
    )

    implementation = catalog.transition_implementation(
        "server-1",
        CapabilityImplementationState.ENABLED,
    )

    assert implementation.state == CapabilityImplementationState.ENABLED

    implementation = catalog.transition_implementation(
        "server-1",
        CapabilityImplementationState.DEGRADED,
    )

    assert implementation.state == CapabilityImplementationState.DEGRADED


def test_invalid_lifecycle_transition_is_rejected() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())
    catalog.register_implementation(
        make_server_implementation(implementation_id="server-1")
    )

    with pytest.raises(InvalidCapabilityStateTransitionError):
        catalog.transition_implementation(
            "server-1",
            CapabilityImplementationState.UNAVAILABLE,
        )


def test_removed_implementation_is_not_routable() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())
    catalog.register_implementation(
        make_server_implementation(implementation_id="server-1")
    )

    catalog.transition_implementation(
        "server-1",
        CapabilityImplementationState.ENABLED,
    )
    catalog.remove_implementation("server-1")

    assert catalog.list_implementations(
        "demo.echo",
        routable_only=True,
    ) == []


def test_client_owner_and_connection_are_enforced() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())
    catalog.register_implementation(make_client_implementation())
    catalog.register_implementation(
        make_server_implementation(implementation_id="server-1")
    )

    policy = CapabilityRoutingPolicy()

    selected = policy.select(
        catalog,
        "demo.echo",
        context=CapabilityRequestContext(
            owner_id="owner-1",
            connection_id="conn-1",
        ),
    )

    assert selected.implementation_id == "client-1"


def test_wrong_client_connection_is_not_authorized() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())
    catalog.register_implementation(make_client_implementation())

    policy = CapabilityRoutingPolicy()

    with pytest.raises(PermissionError):
        policy.select(
            catalog,
            "demo.echo",
            context=CapabilityRequestContext(
                owner_id="owner-1",
                connection_id="wrong-connection",
            ),
        )


def test_required_scopes_are_fail_closed() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(
        make_definition(required_scopes=["capability:execute"])
    )
    catalog.register_implementation(
        make_server_implementation(implementation_id="server-1")
    )
    catalog.transition_implementation(
        "server-1",
        CapabilityImplementationState.ENABLED,
    )

    policy = CapabilityRoutingPolicy()

    with pytest.raises(PermissionError):
        policy.select(
            catalog,
            "demo.echo",
            context=CapabilityRequestContext(
                scopes=frozenset(),
            ),
        )

    selected = policy.select(
        catalog,
        "demo.echo",
        context=CapabilityRequestContext(
            scopes=frozenset({"capability:execute"}),
        ),
    )

    assert selected.implementation_id == "server-1"


def test_routing_is_deterministic_and_supports_preference() -> None:
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())

    for implementation_id in ("server-b", "server-a"):
        implementation = make_server_implementation(
            implementation_id=implementation_id,
        )
        catalog.register_implementation(implementation)
        catalog.transition_implementation(
            implementation_id,
            CapabilityImplementationState.ENABLED,
        )

    policy = CapabilityRoutingPolicy()

    selected = policy.select(
        catalog,
        "demo.echo",
        context=CapabilityRequestContext(),
    )
    assert selected.implementation_id == "server-a"

    selected = policy.select(
        catalog,
        "demo.echo",
        context=CapabilityRequestContext(),
        preferred_implementation_id="server-b",
    )
    assert selected.implementation_id == "server-b"


def test_agent_runtime_does_not_need_to_know_physical_location() -> None:
    """Architecture guard: routing operates on implementations, not drivers."""
    catalog = CapabilityCatalog()
    catalog.register_definition(make_definition())

    client = make_client_implementation(
        implementation_id="client-1",
        owner_id="owner-1",
        connection_id="conn-1",
    )
    catalog.register_implementation(client)
    catalog.transition_implementation(
        "client-1",
        CapabilityImplementationState.ENABLED,
    )

    selected = CapabilityRoutingPolicy().select(
        catalog,
        "demo.echo",
        context=CapabilityRequestContext(
            owner_id="owner-1",
            connection_id="conn-1",
        ),
    )

    assert selected.location == CapabilityExecutionLocation.CLIENT
    assert selected.implementation_id == "client-1"