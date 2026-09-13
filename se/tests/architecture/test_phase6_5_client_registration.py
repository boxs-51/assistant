from __future__ import annotations

import pytest

from src.runtimes.capability.catalog import CapabilityCatalog
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityOwnerType,
)
from src.runtimes.capability.contracts.registration import (
    CapabilityKind,
    CapabilityRegistration,
    ClientCapabilityRegistration,
)
from src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
    ClientRegistrationError,
)
from src.runtimes.connection.registry import ConnectionRegistry


def make_definition(capability_id: str = "desktop.echo") -> CapabilityDefinition:
    return CapabilityDefinition(
        id=capability_id,
        name=capability_id,
        description="Client capability",
        input_schema={"type": "object"},
    )


def make_request(
    *,
    connection_id: str = "conn-1",
    owner_id: str = "user-1",
    location: CapabilityExecutionLocation = CapabilityExecutionLocation.CLIENT,
    owner_type: CapabilityOwnerType = CapabilityOwnerType.CLIENT,
    driver_kind: str = "REMOTE_CLIENT",
) -> ClientCapabilityRegistration:
    return ClientCapabilityRegistration(
        connection_id=connection_id,
        client_id="desktop-01",
        owner_id=owner_id,
        capabilities=[
            CapabilityRegistration(
                definition=make_definition(),
                kind=CapabilityKind.TOOL,
                location=location,
                driver_kind=driver_kind,
                owner_type=owner_type,
                implementation_id="desktop-01:desktop.echo",
            )
        ],
    )


def make_service() -> tuple[
    ClientCapabilityRegistrationService,
    CapabilityCatalog,
    ConnectionRegistry,
]:
    catalog = CapabilityCatalog()
    connections = ConnectionRegistry()
    connections.register(
        "sess-1",
        "user-1",
        connection_id="conn-1",
    )
    connections.activate("conn-1")
    return ClientCapabilityRegistrationService(catalog, connections), catalog, connections


def test_active_client_registration_enables_implementation() -> None:
    service, catalog, _ = make_service()

    registered = service.register(make_request())

    assert len(registered) == 1
    assert registered[0].state.value == "ENABLED"
    assert registered[0].connection_id == "conn-1"
    assert catalog.get_definition("desktop.echo").capability_id == "desktop.echo"


def test_registration_is_idempotent_for_same_payload() -> None:
    service, catalog, _ = make_service()
    request = make_request()

    first = service.register(request)
    second = service.register(request)

    assert second == first
    assert len(catalog.list_implementations("desktop.echo")) == 1


def test_registration_requires_active_owned_connection() -> None:
    service, _, connections = make_service()

    with pytest.raises(ClientRegistrationError, match="not ACTIVE"):
        service.register(make_request(connection_id="unknown"))

        with pytest.raises(PermissionError, match="not owned"):
            service.register(make_request(owner_id="user-2"))

    connections.disconnect("conn-1")
    with pytest.raises(ClientRegistrationError, match="not ACTIVE"):
        service.register(make_request())


def test_registration_rejects_non_client_driver_contract() -> None:
    service, _, _ = make_service()

    with pytest.raises(ValueError, match="location=CLIENT"):
        service.register(make_request(location=CapabilityExecutionLocation.SERVER))

    with pytest.raises(ValueError, match="owner_type=CLIENT"):
        service.register(make_request(owner_type=CapabilityOwnerType.SYSTEM))

    with pytest.raises(ValueError, match="REMOTE_CLIENT"):
        service.register(make_request(driver_kind="PYTHON"))


def test_registration_rejects_empty_identity_and_spoofed_client_metadata() -> None:
    service, _, _ = make_service()

    with pytest.raises(ClientRegistrationError, match="non-empty"):
        service.register(make_request(owner_id=""))

    request = make_request()
    request.capabilities[0].metadata["client_id"] = "other-client"
    registered = service.register(request)
    assert registered[0].metadata["client_id"] == "desktop-01"


def test_registration_rejects_conflicting_duplicate_definitions() -> None:
    service, _, _ = make_service()
    request = make_request()
    duplicate = request.capabilities[0].model_copy(
        update={
            "definition": make_definition().model_copy(
                update={"description": "different"}
            ),
            "implementation_id": "desktop-01:desktop.echo-2",
        }
    )
    request.capabilities.append(duplicate)

    with pytest.raises(ClientRegistrationError, match="conflicting definitions"):
        service.register(request)


def test_removed_implementation_cannot_be_reported_as_registered() -> None:
    service, _, _ = make_service()
    request = make_request()
    service.register(request)
    service.unregister_connection("conn-1")

    with pytest.raises(ValueError, match="Removed implementation"):
        service.register(request)


def test_disconnect_unregisters_implementations_but_keeps_definitions() -> None:
    service, catalog, _ = make_service()
    service.register(make_request())

    assert service.unregister_connection("conn-1") == 1
    implementation = catalog.get_implementation("desktop-01:desktop.echo")
    assert implementation.state.value == "REMOVED"
    assert catalog.list_implementations(
        "desktop.echo",
        routable_only=True,
    ) == []
    assert catalog.contains_definition("desktop.echo")