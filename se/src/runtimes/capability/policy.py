"""Phase 6.1 authorization and routing policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional, Protocol

from .catalog import CapabilityCatalog
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
)
from .contracts.target import (
    CapabilityInvocationTarget,
    FallbackPolicy,
    ResourceScope,
)
from .contracts.definition import (
    CapabilityDefinition,
    CapabilityEffect,
    CapabilityExecutionMode,
    CapabilityKind,
)


class CapabilityAccessProfile(str, Enum):
    NONE = "NONE"
    DIRECT_READ_ONLY = "DIRECT_READ_ONLY"
    AGENT_POLICY = "AGENT_POLICY"


class CapabilityAccessPolicy:
    """Fail-closed model-visibility policy independent of physical routing."""

    @staticmethod
    def allows(definition: CapabilityDefinition, profile: CapabilityAccessProfile) -> bool:
        if profile is CapabilityAccessProfile.NONE:
            return False
        if profile is CapabilityAccessProfile.AGENT_POLICY:
            return True
        if definition.kind is CapabilityKind.AGENT:
            return False
        if definition.execution_mode in {
            CapabilityExecutionMode.CONTEXT_ONLY,
            CapabilityExecutionMode.LONG_RUNNING,
        }:
            return False
        return bool(definition.effects) and definition.effects.issubset({CapabilityEffect.READ})


class ConnectionAvailabilityProvider(Protocol):
    """Minimal lifecycle dependency required by client-capability routing."""

    def is_active(self, connection_id: str) -> bool:
        """Return whether a connection is currently ACTIVE."""


@dataclass(frozen=True)
class CapabilityRequestContext:
    """Security context used by control-plane routing decisions."""

    owner_id: Optional[str] = None
    connection_id: Optional[str] = None
    scopes: FrozenSet[str] = field(default_factory=frozenset)
    target: CapabilityInvocationTarget | None = None


class CapabilityAuthorizationPolicy:
    """Fail-closed authorization policy for capability implementations."""

    def authorize(
        self,
        implementation: CapabilityImplementation,
        *,
        required_scopes: list[str],
        context: CapabilityRequestContext,
        connection_availability: Optional[ConnectionAvailabilityProvider] = None,
    ) -> bool:
        if implementation.location == CapabilityExecutionLocation.CLIENT:
            if not implementation.owner_id:
                return False
            if not implementation.connection_id:
                return False

            if context.owner_id != implementation.owner_id:
                return False

            if context.connection_id != implementation.connection_id:
                return False

            if connection_availability is None:
                return False

            if not connection_availability.is_active(
                implementation.connection_id
            ):
                return False

        required = frozenset(required_scopes)
        if not required.issubset(context.scopes):
            return False

        return True


class CapabilityRoutingPolicy:
    """Deterministic implementation selection.

    The policy does not care how the implementation physically executes.
    SERVER/CLIENT/MCP/DECLARATIVE are all implementation locations.
    """

    def __init__(
        self,
        authorization: Optional[CapabilityAuthorizationPolicy] = None,
        connection_availability: Optional[
            ConnectionAvailabilityProvider
        ] = None,
    ) -> None:
        self._authorization = (
            authorization or CapabilityAuthorizationPolicy()
        )
        self._connection_availability = connection_availability

    def select(
        self,
        catalog: CapabilityCatalog,
        capability_id: str,
        *,
        context: CapabilityRequestContext,
        preferred_implementation_id: Optional[str] = None,
        excluded_implementation_ids: frozenset[str] = frozenset(),
    ) -> CapabilityImplementation:
        definition = catalog.get_definition(capability_id)
        candidates = catalog.list_implementations(
            capability_id,
            routable_only=True,
        )
        candidates = [
            item
            for item in candidates
            if item.implementation_id not in excluded_implementation_ids
        ]

        target = context.target
        if target is not None:
            if (
                target.fallback_policy is FallbackPolicy.NONE
                and excluded_implementation_ids
            ):
                raise PermissionError(
                    "Explicit target with fallback_policy=NONE cannot reroute."
                )
            candidates = [
                item
                for item in candidates
                if self._matches_target(item, target)
            ]
            if (
                target.resource_scope is ResourceScope.CLIENT_LOCAL
                and context.connection_id is not None
            ):
                candidates = [
                    item
                    for item in candidates
                    if item.connection_id == context.connection_id
                ]
        elif context.connection_id is not None:
            same_connection_clients = [
                item
                for item in candidates
                if (
                    item.location == CapabilityExecutionLocation.CLIENT
                    and item.connection_id == context.connection_id
                )
            ]

            server_or_non_client = [
                item
                for item in candidates
                if item.location != CapabilityExecutionLocation.CLIENT
            ]

            # Foreign client is ALWAYS excluded.
            #
            # Same connection gets first priority.
            # Server remains a valid continuation candidate.
            candidates = (
                same_connection_clients
                + server_or_non_client
            )

        if preferred_implementation_id is not None:
            preferred = [
                item
                for item in candidates
                if item.implementation_id == preferred_implementation_id
            ]
            candidates = preferred + [
                item
                for item in candidates
                if item.implementation_id != preferred_implementation_id
            ]

        for implementation in candidates:
            authorization_context = context
            if (
                target is not None
                and target.resource_scope is ResourceScope.CLIENT_LOCAL
                and context.connection_id is None
            ):
                authorization_context = CapabilityRequestContext(
                    owner_id=context.owner_id,
                    connection_id=implementation.connection_id,
                    scopes=context.scopes,
                    target=target,
                )
            if self._authorization.authorize(
                implementation,
                required_scopes=definition.required_scopes,
                context=authorization_context,
                connection_availability=self._connection_availability,
            ):
                return implementation

        raise PermissionError(
            f"No authorized routable implementation for capability: "
            f"{capability_id}"
        )

    @staticmethod
    def _matches_target(
        implementation: CapabilityImplementation,
        target: CapabilityInvocationTarget,
    ) -> bool:
        if target.resource_scope is ResourceScope.CLIENT_LOCAL:
            return (
                implementation.location is CapabilityExecutionLocation.CLIENT
                and str(implementation.metadata.get("client_id") or "")
                == target.stable_client_id
            )

        declared = implementation.metadata.get("resource_scopes")
        if declared is None:
            declared = implementation.metadata.get("resource_scope")
        if isinstance(declared, str):
            scopes = frozenset({declared})
        elif isinstance(declared, (list, tuple, set, frozenset)):
            scopes = frozenset(str(value) for value in declared)
        else:
            scopes = frozenset()
        return target.resource_scope.value in scopes


__all__ = [
    "CapabilityRequestContext",
    "CapabilityAuthorizationPolicy",
    "CapabilityRoutingPolicy",
    "ConnectionAvailabilityProvider",
    "CapabilityAccessProfile",
    "CapabilityAccessPolicy",
]
