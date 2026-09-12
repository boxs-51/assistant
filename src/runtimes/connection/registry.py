"""Phase 6.2 bridge between lifecycle metadata and transport handles."""

from __future__ import annotations

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

    def register(
        self,
        *,
        connection_id: str,
        session_id: str,
        user_id: str,
        socket: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConnectionSnapshot:
        snapshot = self.lifecycle.register(
            connection_id=connection_id,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
        )

        if socket is not None:
            self._sockets[connection_id] = socket

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
        return snapshot

    def get(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        return self.lifecycle.get(connection_id)

    def get_socket(
        self,
        connection_id: str,
    ) -> Optional[Any]:
        return self._sockets.get(connection_id)

    def evict_stale(self):
        stale = self.lifecycle.evict_stale()

        for connection in stale:
            self._sockets.pop(connection.connection_id, None)

        return stale


__all__ = ["ConnectionRegistry"]