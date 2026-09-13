"""Phase 6.2 bridge between lifecycle metadata and transport handles."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from .contracts import ConnectionSnapshot
from .lifecycle import ConnectionLifecycleRegistry


class ConnectionRegistry:
    """Own lifecycle state and optional transport handles.

    Existing websocket/session behavior can continue to use ``get_socket``.
    Phase 6.2 adds connection identity without coupling that state to a
    particular transport implementation.
    """

    def __init__(
        self,
        *,
        stale_after_seconds: float = 90.0,
    ) -> None:
        self.lifecycle = ConnectionLifecycleRegistry(
            stale_after_seconds=stale_after_seconds,
        )
        self._sockets: Dict[str, Any] = {}
        self._session_to_connection: Dict[str, str] = {}
        self._connection_to_session: Dict[str, str] = {}

    def register(
        self,
        session_id: str,
        user_id: str,
        socket: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        connection_id: Optional[str] = None,
    ) -> ConnectionSnapshot:
        if connection_id is None:
            connection_id = f"conn-{uuid.uuid4()}"

        snapshot = self.lifecycle.register(
            connection_id=connection_id,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
        )

        if socket is not None:
            self._sockets[connection_id] = socket

        self._session_to_connection[session_id] = connection_id
        self._connection_to_session[connection_id] = session_id

        return snapshot

    def activate(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        return self.lifecycle.activate(connection_id)

    def heartbeat(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        return self.lifecycle.heartbeat(connection_id)

    def disconnect(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        snapshot = self.lifecycle.disconnect(connection_id)
        self._sockets.pop(connection_id, None)
        return snapshot

    def unregister(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        snapshot = self.lifecycle.remove(connection_id)
        self._sockets.pop(connection_id, None)
        session_id = self._connection_to_session.pop(
            connection_id,
            None,
        )
        if session_id is not None:
            self._session_to_connection.pop(session_id, None)
        return snapshot

    def get(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        return self.lifecycle.get(connection_id)

    def get_socket(
        self,
        identifier: str,
    ) -> Optional[Any]:
        connection_id = identifier

        if identifier not in self._sockets:
            connection_id = self._session_to_connection.get(
                identifier,
                identifier,
            )

        return self._sockets.get(connection_id)

    def require_active_socket(self, connection_id: str) -> Any:
        if not self.lifecycle.is_active(connection_id):
            raise ConnectionStateError(
                f"Connection '{connection_id}' is not ACTIVE"
            )

        socket = self._sockets.get(connection_id)
        if socket is None:
            raise ConnectionSocketUnavailableError(connection_id)
        return socket

    def evict_stale(self):
        stale = self.lifecycle.evict_stale()

        for connection in stale:
            self._sockets.pop(connection.connection_id, None)

        return stale

    def is_active(self, connection_id: str) -> bool:
        return self.lifecycle.is_active(connection_id)

    def resolve_connection_id(self, session_id: str) -> Optional[str]:
        return self._session_to_connection.get(session_id)

    def session_for_connection(
        self,
        connection_id: str,
    ) -> Optional[str]:
        return self._connection_to_session.get(connection_id)


class ConnectionStateError(RuntimeError):
    """Connection cannot be used for realtime invocation."""


class ConnectionSocketUnavailableError(ConnectionStateError):
    """An ACTIVE connection has no transport socket bound to it."""


__all__ = [
    "ConnectionRegistry",
    "ConnectionStateError",
    "ConnectionSocketUnavailableError",
]