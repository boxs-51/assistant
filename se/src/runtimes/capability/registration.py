"""Phase 6.5 client capability self-registration."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from ..connection.contracts import ConnectionNotFoundError
from ..connection.registry import ConnectionRegistry
from .catalog import (
    CapabilityCatalog,
    CapabilityDefinitionConflictError,
    CapabilityImplementationConflictError,
    InvalidCapabilityBindingError,
)
from .contracts.definition import CapabilityDefinition
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


_DEFINITION_PROVENANCE_METADATA_KEYS = frozenset(
    {
        "client_id",
        "physical_tool",
        "physical_version",
        "bind",
        "binding_action",
        "manifest_version",
        "metadata_version",
        "local_name",
        "connection_id",
        "implementation_id",
        "driver_kind",
    }
)


class ClientRegistrationError(ValueError):
    """Base error for invalid client capability registration requests."""


def _logical_definition_contract(
    definition: CapabilityDefinition,
) -> Dict[str, Any]:
    """Return deterministic logical semantics independent of provenance."""
    value = definition.model_dump(mode="json")
    value["id"] = definition.capability_id

    # These describe one physical implementation source, not the logical API.
    value.pop("source", None)
    value.pop("execution_kind", None)

    value["effects"] = sorted(value.get("effects") or [])
    value["required_scopes"] = sorted(
        value.get("required_scopes") or []
    )

    metadata = dict(value.get("metadata") or {})
    for key in _DEFINITION_PROVENANCE_METADATA_KEYS:
        metadata.pop(key, None)
    for key in ("required_permissions", "danger_patterns"):
        if key in metadata:
            metadata[key] = sorted(metadata[key] or [])
    value["metadata"] = metadata
    return value


def _implementation_contract(
    implementation: CapabilityImplementation,
) -> Dict[str, Any]:
    """Return implementation identity/ownership semantics without lifecycle state."""
    value = implementation.model_dump(mode="json")
    value.pop("state", None)
    return value


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

        # Deep-own the caller payload before any object from it can become
        # catalog state. CapabilityDefinition is mutable and the catalog stores
        # definition objects by reference, so retaining request-owned models
        # would allow out-of-band mutation after a successful registration.
        owned_request = request.model_copy(deep=True)

        implementations = [
            self._build_implementation(owned_request, item)
            for item in owned_request.capabilities
        ]
        self._validate_batch(owned_request, implementations)

        definitions: Dict[str, CapabilityDefinition] = {}
        for item in owned_request.capabilities:
            definitions.setdefault(
                item.definition.capability_id,
                item.definition,
            )

        self._validate_existing_definitions(definitions)
        self._validate_existing_implementations(implementations)

        # Commit only entries proven absent during preflight. Equivalent
        # definitions/implementations remain canonical and are reused.
        for capability_id, definition in definitions.items():
            if not self.catalog.contains_definition(capability_id):
                self.catalog.register_definition(definition)

        registered: List[CapabilityImplementation] = []
        for implementation in implementations:
            if self.catalog.contains_implementation(
                implementation.implementation_id
            ):
                registered.append(
                    self.catalog.get_implementation(
                        implementation.implementation_id
                    )
                )
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
            self.catalog.remove_implementation(
                implementation.implementation_id
            )
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

        manifest_version = registration.metadata.get(
            "manifest_version"
        )
        if manifest_version is not None and manifest_version != "2.0":
            raise ClientRegistrationError(
                "Unsupported client capability manifest_version: "
                f"{manifest_version!r}"
            )

        if manifest_version == "2.0":
            definition = registration.definition
            if (
                definition.source != "LOCAL"
                or definition.execution_kind != "PYTHON"
            ):
                raise ClientRegistrationError(
                    "Canonical Metadata V2 client definitions require "
                    "source=LOCAL and execution_kind=PYTHON"
                )
            leaked_provenance = set(definition.metadata).intersection(
                _DEFINITION_PROVENANCE_METADATA_KEYS
            )
            if leaked_provenance:
                raise ClientRegistrationError(
                    "Canonical Metadata V2 definition metadata cannot contain "
                    "implementation provenance: "
                    + ", ".join(sorted(leaked_provenance))
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
        if registration.connection_id not in {
            None,
            request.connection_id,
        }:
            raise PermissionError(
                f"Capability '{registration.implementation_id}' "
                "connection mismatch"
            )
        if not registration.implementation_id.strip():
            raise ClientRegistrationError(
                "Client registration requires non-empty implementation_id"
            )

        metadata = deepcopy(registration.metadata)
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
        implementation_ids = [
            item.implementation_id for item in implementations
        ]
        if len(set(implementation_ids)) != len(implementation_ids):
            raise ClientRegistrationError(
                "Client registration contains duplicate implementation_id "
                "values"
            )

        definitions: Dict[str, CapabilityDefinition] = {}
        for registration in request.capabilities:
            capability_id = registration.definition.capability_id
            if capability_id in definitions:
                raise ClientRegistrationError(
                    "Client registration contains duplicate capability_id "
                    f"values: {capability_id}"
                )
            definitions[capability_id] = registration.definition

    def _validate_existing_definitions(
        self,
        definitions: Dict[str, CapabilityDefinition],
    ) -> None:
        for capability_id, definition in definitions.items():
            if not self.catalog.contains_definition(capability_id):
                continue

            existing = self.catalog.get_definition(capability_id)
            if (
                _logical_definition_contract(existing)
                != _logical_definition_contract(definition)
            ):
                raise CapabilityDefinitionConflictError(
                    "Capability definition already exists with different "
                    f"contract: {capability_id}"
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
            if existing.state == CapabilityImplementationState.REMOVED:
                raise CapabilityImplementationConflictError(
                    "Removed implementation cannot be re-registered: "
                    f"{implementation.implementation_id}"
                )

            if (
                _implementation_contract(existing)
                != _implementation_contract(implementation)
            ):
                raise CapabilityImplementationConflictError(
                    "Implementation already exists with a different "
                    f"contract: {implementation.implementation_id}"
                )
