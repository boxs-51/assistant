"""Phase 6.5 client capability self-registration."""

from __future__ import annotations

from typing import List

from ..connection.contracts import ConnectionNotFoundError
from ..connection.registry import ConnectionRegistry
from .catalog import (
    CapabilityCatalog,
    CapabilityDefinitionConflictError,
    CapabilityImplementationConflictError,
    InvalidCapabilityBindingError,
)
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from .contracts.registration import (
    CapabilityRegistration,
    ClientCapabilityRegistration,
)


class ClientRegistrationError(ValueError):
    """Base error for invalid client capability registration requests."""


class ClientCapabilityRegistrationService:
    """Validate and persist capabilities advertised by an active client."""

    def __init__(
        self,
        catalog: CapabilityCatalog,
        connections: ConnectionRegistry,
    ) -> None:
        self.catalog = catalog
        self.connections = connections

    def register(
        self,
        request: ClientCapabilityRegistration,
    ) -> List[CapabilityImplementation]:
        try:
            snapshot = self.connections.get(request.connection_id)
        except ConnectionNotFoundError as exc:
            raise ClientRegistrationError(
                f"Connection '{request.connection_id}' is not ACTIVE"
            ) from exc
        if not snapshot.is_usable:
            raise ClientRegistrationError(
                f"Connection '{request.connection_id}' is not ACTIVE"
            )
        if snapshot.user_id != request.owner_id:
            raise PermissionError(
                f"Connection '{request.connection_id}' is not owned by "
                f"'{request.owner_id}'"
            )

        implementations = [
            self._build_implementation(request, item)
            for item in request.capabilities
        ]
        self._validate_batch(implementations)

        definitions = {
            item.definition.capability_id: item.definition
            for item in request.capabilities
        }
        self._validate_existing_definitions(definitions)
        self._validate_existing_implementations(implementations)

        for definition in definitions.values():
            self.catalog.register_definition(definition)

        registered: List[CapabilityImplementation] = []
        for implementation in implementations:
            if self.catalog.contains_implementation(
                implementation.implementation_id
            ):
                existing = self.catalog.get_implementation(implementation.implementation_id)
                registered.append(existing)
                continue

            self.catalog.register_implementation(implementation)
            registered.append(
                self.catalog.transition_implementation(
                    implementation.implementation_id,
                    CapabilityImplementationState.ENABLED,
                )
            )

        return registered

    def unregister_connection(self, connection_id: str) -> int:
        implementations = self.catalog.list_implementations_for_connection(
            connection_id
        )
        for implementation in implementations:
            self.catalog.remove_implementation(implementation.implementation_id)
        return len(implementations)

    @staticmethod
    def _build_implementation(
        request: ClientCapabilityRegistration,
        registration: CapabilityRegistration,
    ) -> CapabilityImplementation:
        if registration.location != CapabilityExecutionLocation.CLIENT:
            raise InvalidCapabilityBindingError(
                "Client self-registration requires location=CLIENT"
            )
        if registration.owner_type != CapabilityOwnerType.CLIENT:
            raise InvalidCapabilityBindingError(
                "Client self-registration requires owner_type=CLIENT"
            )
        if registration.driver_kind != "REMOTE_CLIENT":
            raise InvalidCapabilityBindingError(
                "Client self-registration requires driver_kind=REMOTE_CLIENT"
            )
        if registration.owner_id not in {None, request.owner_id}:
            raise PermissionError(
                f"Capability '{registration.implementation_id}' owner mismatch"
            )
        if registration.connection_id not in {None, request.connection_id}:
            raise PermissionError(
                f"Capability '{registration.implementation_id}' connection mismatch"
            )

        metadata = dict(registration.metadata)
        metadata.setdefault("client_id", request.client_id)
        return CapabilityImplementation.from_definition(
            registration.definition,
            implementation_id=registration.implementation_id,
            location=registration.location,
            driver_kind=registration.driver_kind,
            owner_type=CapabilityOwnerType.CLIENT,
            owner_id=request.owner_id,
            connection_id=request.connection_id,
            metadata=metadata,
        )

    @staticmethod
    def _validate_batch(
        implementations: List[CapabilityImplementation],
    ) -> None:
        implementation_ids = [item.implementation_id for item in implementations]
        if len(set(implementation_ids)) != len(implementation_ids):
            raise ClientRegistrationError(
                "Client registration contains duplicate implementation_id values"
            )

    def _validate_existing_definitions(self, definitions) -> None:
        for capability_id, definition in definitions.items():
            if not self.catalog.contains_definition(capability_id):
                continue
            if self.catalog.get_definition(capability_id) != definition:
                raise CapabilityDefinitionConflictError(
                    f"Capability definition already exists with different contract: "
                    f"{capability_id}"
                )

    def _validate_existing_implementations(
        self,
        implementations: List[CapabilityImplementation],
    ) -> None:
        for implementation in implementations:
            if not self.catalog.contains_implementation(
                implementation.implementation_id
            ):
                continue
            existing = self.catalog.get_implementation(
                implementation.implementation_id
            )
            comparable = existing.model_copy(
                update={"state": implementation.state}
            )
            if comparable != implementation:
                raise CapabilityImplementationConflictError(
                    f"Implementation already exists with a different contract: "
                    f"{implementation.implementation_id}"
                )