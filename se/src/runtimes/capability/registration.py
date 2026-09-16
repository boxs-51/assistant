"""Phase 6.5 client capability self-registration."""

from __future__ import annotations

from typing import List, Dict

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
from .contracts.definition import CapabilityDefinition

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
        if not request.client_id.strip() or not request.owner_id.strip():
            raise ClientRegistrationError(
                "Client registration requires non-empty client_id and owner_id"
            )
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
        self._validate_batch(request, implementations)

        definitions = {
            item.definition.capability_id: item.definition
            for item in request.capabilities
        }
        self._validate_existing_definitions(definitions, allow_update=True)
        self._validate_existing_implementations(implementations, allow_update=True)

        for definition in definitions.values():
            self.catalog.register_definition(definition, allow_update=True)

        registered: List[CapabilityImplementation] = []
        for implementation in implementations:
            if self.catalog.contains_implementation(
                implementation.implementation_id
            ):
                existing = self.catalog.get_implementation(implementation.implementation_id)
                registered.append(existing)
                continue

            self.catalog.register_implementation(implementation, allow_update=True)
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
        if not registration.implementation_id.strip():
            raise ClientRegistrationError(
                "Client registration requires non-empty implementation_id"
            )

        metadata = dict(registration.metadata)
        metadata["client_id"] = request.client_id
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
        request: ClientCapabilityRegistration,
        implementations: List[CapabilityImplementation],
    ) -> None:
        implementation_ids = [item.implementation_id for item in implementations]
        if len(set(implementation_ids)) != len(implementation_ids):
            raise ClientRegistrationError(
                "Client registration contains duplicate implementation_id values"
            )
        definitions = {}
        for registration in request.capabilities:
            capability_id = registration.definition.capability_id
            existing = definitions.get(capability_id)
            if existing is not None and existing != registration.definition:
                raise ClientRegistrationError(
                    f"Client registration contains conflicting definitions: "
                    f"{capability_id}"
                )
            definitions[capability_id] = registration.definition

    def _validate_existing_definitions(
            self, 
            definitions: Dict[str, CapabilityDefinition],
            *, 
            allow_update: bool = False,
    ) -> None:
        for capability_id, definition in definitions.items():
            if not self.catalog.contains_definition(capability_id):
                continue
            if self.catalog.get_definition(capability_id) != definition and not allow_update:
                raise CapabilityDefinitionConflictError(
                    f"Capability definition already exists with different contract: "
                    f"{capability_id}"
                )

    def _validate_existing_implementations(
        self,
        implementations: List[CapabilityImplementation],
        *,
        allow_update: bool = False
    ) -> None:
        for implementation in implementations:
            if not self.catalog.contains_implementation(
                implementation.implementation_id
            ):
                continue
            existing = self.catalog.get_implementation(
                implementation.implementation_id
            )
            if existing.state == CapabilityImplementationState.REMOVED:
                raise CapabilityImplementationConflictError(
                    f"Removed implementation cannot be re-registered: "
                    f"{implementation.implementation_id}"
                )
            comparable = existing.model_copy(
                update={"state": implementation.state}
            )
            if comparable != implementation and not allow_update:
                raise CapabilityImplementationConflictError(
                    f"Implementation already exists with a different contract: "
                    f"{implementation.implementation_id}"
                )