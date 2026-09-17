"""Phase 6.1 capability control-plane catalog.

The catalog is intentionally separate from the legacy CapabilityRegistry.
CapabilityRegistry owns executable server-side drivers; this catalog owns
logical definitions and concrete implementation metadata.
"""

from __future__ import annotations

from threading import RLock
from typing import Dict, List

from .contracts.definition import CapabilityDefinition, validate_input_schema
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)


class CapabilityCatalogError(ValueError):
    """Base error for capability catalog violations."""


class CapabilityDefinitionConflictError(CapabilityCatalogError):
    """Raised when an existing capability ID is registered with another contract."""


class CapabilityImplementationConflictError(CapabilityCatalogError):
    """Raised when an implementation ID is already registered."""


class CapabilityNotFoundError(CapabilityCatalogError):
    """Raised when a referenced capability does not exist."""


class CapabilityImplementationNotFoundError(CapabilityCatalogError):
    """Raised when a referenced implementation does not exist."""


class InvalidCapabilityBindingError(CapabilityCatalogError):
    """Raised when implementation ownership/location binding is invalid."""


class InvalidCapabilityStateTransitionError(CapabilityCatalogError):
    """Raised when an implementation lifecycle transition is invalid."""


class CapabilityCatalog:
    """Thread-safe catalog of definitions and multiple implementations.

    A logical capability may have multiple concrete implementations:

        capability_id
          ├── server implementation
          ├── client implementation A
          └── MCP implementation

    The catalog does not execute anything.
    """

    _ROUTABLE_STATES = frozenset(
        {
            CapabilityImplementationState.ENABLED,
            CapabilityImplementationState.DEGRADED,
        }
    )

    _ALLOWED_STATE_TRANSITIONS = {
        CapabilityImplementationState.REGISTERED: frozenset(
            {
                CapabilityImplementationState.ENABLED,
                CapabilityImplementationState.DISABLED,
                CapabilityImplementationState.REMOVED,
            }
        ),
        CapabilityImplementationState.ENABLED: frozenset(
            {
                CapabilityImplementationState.DEGRADED,
                CapabilityImplementationState.DISABLED,
                CapabilityImplementationState.UNAVAILABLE,
                CapabilityImplementationState.REMOVED,
            }
        ),
        CapabilityImplementationState.DEGRADED: frozenset(
            {
                CapabilityImplementationState.ENABLED,
                CapabilityImplementationState.DISABLED,
                CapabilityImplementationState.UNAVAILABLE,
                CapabilityImplementationState.REMOVED,
            }
        ),
        CapabilityImplementationState.UNAVAILABLE: frozenset(
            {
                CapabilityImplementationState.ENABLED,
                CapabilityImplementationState.DEGRADED,
                CapabilityImplementationState.DISABLED,
                CapabilityImplementationState.REMOVED,
            }
        ),
        CapabilityImplementationState.DISABLED: frozenset(
            {
                CapabilityImplementationState.ENABLED,
                CapabilityImplementationState.DEGRADED,
                CapabilityImplementationState.REMOVED,
            }
        ),
        CapabilityImplementationState.REMOVED: frozenset(),
    }

    def __init__(self) -> None:
        self._lock = RLock()
        self._definitions: Dict[str, CapabilityDefinition] = {}
        self._implementations: Dict[str, CapabilityImplementation] = {}
        self._by_capability: Dict[str, set[str]] = {}

    def register_definition(
        self,
        definition: CapabilityDefinition,
        *,
        allow_update: bool = False,
    ) -> CapabilityDefinition:
        """Register one logical capability definition.

        Re-registering the exact same contract is idempotent.
        Re-registering the same ID with a different contract is rejected.
        """
        validate_input_schema(definition.input_schema)
        capability_id = definition.capability_id

        with self._lock:
            existing = self._definitions.get(capability_id)
            if existing is not None:
                if existing != definition:
                    if not allow_update:
                        raise CapabilityDefinitionConflictError(
                            f"Capability definition already exists with different "
                            f"contract: {capability_id}"
                        )
                    self._definitions[capability_id] = definition
                    return definition
                return existing

            self._definitions[capability_id] = definition
            self._by_capability.setdefault(capability_id, set())
            return definition

    def get_definition(self, capability_id: str) -> CapabilityDefinition:
        with self._lock:
            try:
                return self._definitions[capability_id]
            except KeyError as exc:
                raise CapabilityNotFoundError(
                    f"Unknown capability: {capability_id}"
                ) from exc

    def register_implementation(
        self,
        implementation: CapabilityImplementation,
        *,
        allow_update: bool = False,
    ) -> CapabilityImplementation:
        """Register a concrete implementation without replacing siblings."""
        with self._lock:
            if implementation.capability_id not in self._definitions:
                raise CapabilityNotFoundError(
                    f"Unknown capability: {implementation.capability_id}"
                )

            definition = self._definitions[implementation.capability_id]

            if implementation.version != definition.version:
                raise CapabilityCatalogError(
                    f"Implementation '{implementation.implementation_id}' "
                    f"version {implementation.version!r} does not match "
                    f"capability '{implementation.capability_id}' "
                    f"version {definition.version!r}"
                )

            self._validate_binding(implementation)

            if implementation.implementation_id in self._implementations and not allow_update:
                raise CapabilityImplementationConflictError(
                    f"Implementation already exists: "
                    f"{implementation.implementation_id}"
                )

            self._implementations[
                implementation.implementation_id
            ] = implementation
            self._by_capability.setdefault(
                implementation.capability_id, set()
            ).add(implementation.implementation_id)

            return implementation

    def get_implementation(
        self,
        implementation_id: str,
    ) -> CapabilityImplementation:
        with self._lock:
            try:
                return self._implementations[implementation_id]
            except KeyError as exc:
                raise CapabilityImplementationNotFoundError(
                    f"Unknown implementation: {implementation_id}"
                ) from exc

    def list_implementations(
        self,
        capability_id: str,
        *,
        routable_only: bool = False,
    ) -> List[CapabilityImplementation]:
        """Return implementations in deterministic implementation-ID order."""
        with self._lock:
            if capability_id not in self._definitions:
                raise CapabilityNotFoundError(
                    f"Unknown capability: {capability_id}"
                )

            implementation_ids = self._by_capability.get(
                capability_id,
                set(),
            )

            implementations = [
                self._implementations[implementation_id]
                for implementation_id in sorted(implementation_ids)
            ]

            if routable_only:
                implementations = [
                    item
                    for item in implementations
                    if item.state in self._ROUTABLE_STATES
                ]

            return implementations

    def list_implementations_for_connection(
        self,
        connection_id: str,
    ) -> List[CapabilityImplementation]:
        """Return all implementations bound to one client connection."""
        with self._lock:
            return sorted(
                (
                    item
                    for item in self._implementations.values()
                    if item.connection_id == connection_id
                ),
                key=lambda item: item.implementation_id,
            )

    def transition_implementation(
        self,
        implementation_id: str,
        new_state: CapabilityImplementationState,
    ) -> CapabilityImplementation:
        """Apply one deterministic lifecycle transition."""
        with self._lock:
            current = self.get_implementation(implementation_id)

            if current.state == new_state:
                return current

            allowed = self._ALLOWED_STATE_TRANSITIONS[current.state]
            if new_state not in allowed:
                raise InvalidCapabilityStateTransitionError(
                    f"Invalid implementation state transition: "
                    f"{current.state.value} -> {new_state.value}"
                )

            updated = current.model_copy(update={"state": new_state})
            self._implementations[implementation_id] = updated
            return updated

    def remove_implementation(
        self,
        implementation_id: str,
    ) -> CapabilityImplementation:
        """Transition an implementation to the terminal REMOVED state."""
        return self.transition_implementation(
            implementation_id,
            CapabilityImplementationState.REMOVED,
        )

    def contains_definition(self, capability_id: str) -> bool:
        with self._lock:
            return capability_id in self._definitions

    def list_definitions(self) -> List[CapabilityDefinition]:
        """Return logical capability definitions in stable ID order."""
        with self._lock:
            return [self._definitions[key] for key in sorted(self._definitions)]

    def contains_implementation(self, implementation_id: str) -> bool:
        with self._lock:
            return implementation_id in self._implementations

    @staticmethod
    def _validate_binding(
        implementation: CapabilityImplementation,
    ) -> None:
        if implementation.location == CapabilityExecutionLocation.CLIENT:
            if implementation.owner_type != CapabilityOwnerType.CLIENT:
                raise InvalidCapabilityBindingError(
                    "CLIENT implementation requires owner_type=CLIENT"
                )
            if not implementation.owner_id:
                raise InvalidCapabilityBindingError(
                    "CLIENT implementation requires owner_id"
                )
            if not implementation.connection_id:
                raise InvalidCapabilityBindingError(
                    "CLIENT implementation requires connection_id"
                )

        if (
            implementation.location == CapabilityExecutionLocation.SERVER
            and implementation.connection_id is not None
        ):
            raise InvalidCapabilityBindingError(
                "SERVER implementation must not bind to connection_id"
            )


__all__ = [
    "CapabilityCatalog",
    "CapabilityCatalogError",
    "CapabilityDefinitionConflictError",
    "CapabilityImplementationConflictError",
    "CapabilityNotFoundError",
    "CapabilityImplementationNotFoundError",
    "InvalidCapabilityBindingError",
    "InvalidCapabilityStateTransitionError",
]
