"""Phase 6.1 authorization and routing policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Optional

from .catalog import CapabilityCatalog
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
)


@dataclass(frozen=True)
class CapabilityRequestContext:
    """Security context used by control-plane routing decisions."""

    owner_id: Optional[str] = None
    connection_id: Optional[str] = None
    scopes: FrozenSet[str] = field(default_factory=frozenset)


class CapabilityAuthorizationPolicy:
    """Fail-closed authorization policy for capability implementations."""

    def authorize(
        self,
        implementation: CapabilityImplementation,
        *,
        required_scopes: list[str],
        context: CapabilityRequestContext,
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
    ) -> None:
        self._authorization = (
            authorization or CapabilityAuthorizationPolicy()
        )

    def select(
        self,
        catalog: CapabilityCatalog,
        capability_id: str,
        *,
        context: CapabilityRequestContext,
        preferred_implementation_id: Optional[str] = None,
    ) -> CapabilityImplementation:
        definition = catalog.get_definition(capability_id)
        candidates = catalog.list_implementations(
            capability_id,
            routable_only=True,
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
            if self._authorization.authorize(
                implementation,
                required_scopes=definition.required_scopes,
                context=context,
            ):
                return implementation

        raise PermissionError(
            f"No authorized routable implementation for capability: "
            f"{capability_id}"
        )


__all__ = [
    "CapabilityRequestContext",
    "CapabilityAuthorizationPolicy",
    "CapabilityRoutingPolicy",
]