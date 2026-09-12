"""Phase 6.2 connection lifecycle contracts.

This module contains lifecycle state and immutable connection snapshots.
It intentionally does not contain invocation/multiplexing semantics.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConnectionState(str, Enum):
    """Lifecycle state of one transport connection."""

    REGISTERED = "REGISTERED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    REMOVED = "REMOVED"


class ConnectionStateTransitionError(ValueError):
    """Raised when an invalid connection lifecycle transition is attempted."""


class ConnectionNotFoundError(KeyError):
    """Raised when a connection ID is unknown."""


class ConnectionSnapshot(BaseModel):
    """Serializable connection lifecycle snapshot.

    ``session_id`` remains the transport/session identifier used by the
    existing ConnectionRuntime. ``connection_id`` is the Phase 6 logical
    connection identity used to bind capability implementations.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    connection_id: str
    session_id: str
    user_id: str

    state: ConnectionState = ConnectionState.REGISTERED

    connected_at: float
    last_heartbeat_at: float
    stale_after_seconds: float = Field(gt=0)

    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        return self.state == ConnectionState.ACTIVE


ALLOWED_CONNECTION_TRANSITIONS = {
    ConnectionState.REGISTERED: frozenset(
        {
            ConnectionState.ACTIVE,
            ConnectionState.DISCONNECTED,
            ConnectionState.REMOVED,
        }
    ),
    ConnectionState.ACTIVE: frozenset(
        {
            ConnectionState.STALE,
            ConnectionState.DISCONNECTED,
            ConnectionState.REMOVED,
        }
    ),
    ConnectionState.STALE: frozenset(
        {
            ConnectionState.ACTIVE,
            ConnectionState.DISCONNECTED,
            ConnectionState.REMOVED,
        }
    ),
    ConnectionState.DISCONNECTED: frozenset(
        {
            ConnectionState.REMOVED,
        }
    ),
    ConnectionState.REMOVED: frozenset(),
}


__all__ = [
    "ConnectionState",
    "ConnectionStateTransitionError",
    "ConnectionNotFoundError",
    "ConnectionSnapshot",
    "ALLOWED_CONNECTION_TRANSITIONS",
]