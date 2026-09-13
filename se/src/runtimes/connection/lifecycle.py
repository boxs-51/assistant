"""Phase 6.2 connection lifecycle registry.

The registry is transport/control-plane state only.
It does not send invocations and does not know about AgentRuntime.
"""

from __future__ import annotations

import time
from threading import RLock
from typing import Dict, List, Optional

from .contracts import (
    ALLOWED_CONNECTION_TRANSITIONS,
    ConnectionNotFoundError,
    ConnectionSnapshot,
    ConnectionState,
    ConnectionStateTransitionError,
)


class ConnectionLifecycleRegistry:
    """Thread-safe source of truth for connection lifecycle state."""

    def __init__(
        self,
        *,
        stale_after_seconds: float = 90.0,
        clock=time.monotonic,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be > 0")

        self._lock = RLock()
        self._connections: Dict[str, ConnectionSnapshot] = {}
        self._stale_after_seconds = float(stale_after_seconds)
        self._clock = clock

    def register(
        self,
        *,
        connection_id: str,
        session_id: str,
        user_id: str,
        metadata: Optional[dict] = None,
        now: Optional[float] = None,
    ) -> ConnectionSnapshot:
        timestamp = self._now() if now is None else float(now)

        with self._lock:
            existing = self._connections.get(connection_id)

            if existing is not None:
                raise ValueError(
                    f"Connection ID cannot be reused: {connection_id}"
                )

            snapshot = ConnectionSnapshot(
                connection_id=connection_id,
                session_id=session_id,
                user_id=user_id,
                state=ConnectionState.REGISTERED,
                connected_at=timestamp,
                last_heartbeat_at=timestamp,
                stale_after_seconds=self._stale_after_seconds,
                metadata=dict(metadata or {}),
            )
            self._connections[connection_id] = snapshot
            return snapshot

    def activate(
        self,
        connection_id: str,
        *,
        now: Optional[float] = None,
    ) -> ConnectionSnapshot:
        return self.transition(
            connection_id,
            ConnectionState.ACTIVE,
            now=now,
        )

    def heartbeat(
        self,
        connection_id: str,
        *,
        now: Optional[float] = None,
    ) -> ConnectionSnapshot:
        timestamp = self._now() if now is None else float(now)

        with self._lock:
            current = self._get(connection_id)

            if current.state not in {
                ConnectionState.ACTIVE,
                ConnectionState.STALE,
            }:
                raise ConnectionStateTransitionError(
                    f"Heartbeat requires ACTIVE or STALE connection; "
                    f"got {current.state.value} for {connection_id}"
                )

            updated = current.model_copy(
                update={
                    "state": ConnectionState.ACTIVE,
                    "last_heartbeat_at": timestamp,
                }
            )
            self._connections[connection_id] = updated
            return updated

    def mark_stale(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        with self._lock:
            current = self._get(connection_id)
            updated = self._transition(current, ConnectionState.STALE)
            self._connections[connection_id] = updated
            return updated

    def disconnect(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        with self._lock:
            current = self._get(connection_id)
            updated = self._transition(
                current,
                ConnectionState.DISCONNECTED,
            )
            self._connections[connection_id] = updated
            return updated

    def remove(
        self,
        connection_id: str,
    ) -> ConnectionSnapshot:
        with self._lock:
            current = self._get(connection_id)
            updated = self._transition(
                current,
                ConnectionState.REMOVED,
            )
            self._connections[connection_id] = updated
            return updated

    def transition(
        self,
        connection_id: str,
        new_state: ConnectionState,
        *,
        now: Optional[float] = None,
    ) -> ConnectionSnapshot:
        with self._lock:
            current = self._get(connection_id)
            updated = self._transition(current, new_state)

            if now is not None and new_state == ConnectionState.ACTIVE:
                updated = updated.model_copy(
                    update={"last_heartbeat_at": float(now)}
                )

            self._connections[connection_id] = updated
            return updated

    def get(self, connection_id: str) -> ConnectionSnapshot:
        with self._lock:
            return self._get(connection_id)

    def list(
        self,
        *,
        state: Optional[ConnectionState] = None,
    ) -> List[ConnectionSnapshot]:
        with self._lock:
            result = list(self._connections.values())

            if state is not None:
                result = [
                    connection
                    for connection in result
                    if connection.state == state
                ]

            return sorted(
                result,
                key=lambda connection: connection.connection_id,
            )

    def evict_stale(
        self,
        *,
        now: Optional[float] = None,
    ) -> List[ConnectionSnapshot]:
        """Mark expired ACTIVE connections as STALE.

        This method deliberately does not remove entries. The logical
        connection remains observable so higher layers can deterministically
        observe that it became stale.
        """
        timestamp = self._now() if now is None else float(now)
        stale: List[ConnectionSnapshot] = []

        with self._lock:
            for connection_id, current in list(self._connections.items()):
                if current.state != ConnectionState.ACTIVE:
                    continue

                if (
                    timestamp - current.last_heartbeat_at
                    >= current.stale_after_seconds
                ):
                    updated = self._transition(
                        current,
                        ConnectionState.STALE,
                    )
                    self._connections[connection_id] = updated
                    stale.append(updated)

        return stale

    def is_active(self, connection_id: str) -> bool:
        with self._lock:
            current = self._get(connection_id)
            return current.state == ConnectionState.ACTIVE

    def _get(self, connection_id: str) -> ConnectionSnapshot:
        try:
            return self._connections[connection_id]
        except KeyError as exc:
            raise ConnectionNotFoundError(connection_id) from exc

    def _transition(
        self,
        current: ConnectionSnapshot,
        new_state: ConnectionState,
    ) -> ConnectionSnapshot:
        if current.state == new_state:
            return current

        allowed = ALLOWED_CONNECTION_TRANSITIONS[current.state]
        if new_state not in allowed:
            raise ConnectionStateTransitionError(
                f"Invalid connection state transition: "
                f"{current.state.value} -> {new_state.value}"
            )

        return current.model_copy(update={"state": new_state})

    def _now(self) -> float:
        return float(self._clock())


__all__ = ["ConnectionLifecycleRegistry"]