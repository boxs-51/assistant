"""Phase 6.1 authorization and target-aware routing policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Optional, Protocol

from .catalog import CapabilityCatalog
from .contracts.definition import (
    CapabilityDefinition,
    CapabilityEffect,
    CapabilityExecutionMode,
    CapabilityKind,
)
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
)
from .contracts.target import (
    CapabilityInvocationTarget,
    FallbackPolicy,
    ResourceScope,
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


def implementation_supports_target(
    implementation: CapabilityImplementation,
    target: CapabilityInvocationTarget | None,
) -> bool:
    """Return whether one implementation preserves explicit target semantics."""

    if target is None:
        return True

    client_id = str(implementation.metadata.get("client_id") or "")
    if target.resource_scope is ResourceScope.CLIENT_LOCAL:
        return (
            implementation.location is CapabilityExecutionLocation.CLIENT
            and bool(client_id)
            and client_id == target.stable_client_id
        )

    if target.stable_client_id is not None:
        if implementation.location is not CapabilityExecutionLocation.CLIENT:
            return False
        if client_id != target.stable_client_id:
            return False

    declared = implementation.metadata.get("resource_scopes", ())
    if isinstance(declared, str):
        declared_scopes = {declared}
    else:
        try:
            declared_scopes = {str(value) for value in declared}
        except TypeError:
            return False
    return target.resource_scope.value in declared_scopes


@dataclass(frozen=True)
class CapabilityRequestContext:
    """Security and semantic target context for routing decisions."""

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
            if not implementation.owner_id or not implementation.connection_id:
                return False
            if context.owner_id != implementation.owner_id:
                return False

            if context.target is None:
                if context.connection_id != implementation.connection_id:
                    return False
            elif not implementation_supports_target(implementation, context.target):
                return False

            if connection_availability is None:
                return False
            if not connection_availability.is_active(implementation.connection_id):
                return False

        required = frozenset(required_scopes)
        if not required.issubset(context.scopes):
            return False
        return True


class CapabilityRoutingPolicy:
    """Deterministic physical selection after semantic target resolution."""

    def __init__(
        self,
        authorization: Optional[CapabilityAuthorizationPolicy] = None,
        connection_availability: Optional[ConnectionAvailabilityProvider] = None,
    ) -> None:
        self._authorization = authorization or CapabilityAuthorizationPolicy()
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
        candidates = catalog.list_implementations(capability_id, routable_only=True)
        candidates = [
            item
            for item in candidates
            if item.implementation_id not in excluded_implementation_ids
        ]

        if context.target is not None:
            candidates = [
                item for item in candidates
                if implementation_supports_target(item, context.target)
            ]
            if (
                excluded_implementation_ids
                and context.target.fallback_policy is FallbackPolicy.NONE
            ):
                candidates = []

            if context.connection_id is not None:
                same_connection_clients = [
                    item
                    for item in candidates
                    if (
                        item.location is CapabilityExecutionLocation.CLIENT
                        and item.connection_id == context.connection_id
                    )
                ]
                remaining = [
                    item for item in candidates
                    if item not in same_connection_clients
                ]
                candidates = same_connection_clients + remaining
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
            candidates = same_connection_clients + server_or_non_client

        if preferred_implementation_id is not None:
            preferred = [
                item
                for item in candidates
                if item.implementation_id == preferred_implementation_id
            ]
            if (
                context.target is not None
                and context.target.fallback_policy is FallbackPolicy.NONE
            ):
                candidates = preferred
            else:
                candidates = preferred + [
                    item
                    for item in candidates
                    if item.implementation_id != preferred_implementation_id
                ]

        for implementation in candidates:
            if self._authorization.authorize(
                implementation,
                required_scopes=definition.required_scopes,
                context=context,
                connection_availability=self._connection_availability,
            ):
                return implementation

        raise PermissionError(
            f"No authorized routable implementation for capability: {capability_id}"
        )


__all__ = [
    "CapabilityRequestContext",
    "CapabilityAuthorizationPolicy",
    "CapabilityRoutingPolicy",
    "ConnectionAvailabilityProvider",
    "CapabilityAccessProfile",
    "CapabilityAccessPolicy",
    "implementation_supports_target",
]
