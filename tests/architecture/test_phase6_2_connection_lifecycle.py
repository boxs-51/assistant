from __future__ import annotations

import pytest

from src.runtimes.connection.contracts import (
    ConnectionNotFoundError,
    ConnectionState,
    ConnectionStateTransitionError,
)
from src.runtimes.connection.lifecycle import ConnectionLifecycleRegistry


class FakeClock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def make_registry(
    clock: FakeClock,
    *,
    stale_after_seconds: float = 30.0,
) -> ConnectionLifecycleRegistry:
    return ConnectionLifecycleRegistry(
        stale_after_seconds=stale_after_seconds,
        clock=clock,
    )


def test_register_creates_registered_connection() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    connection = registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )

    assert connection.connection_id == "conn-1"
    assert connection.session_id == "sess-1"
    assert connection.user_id == "user-1"
    assert connection.state == ConnectionState.REGISTERED


def test_register_duplicate_connection_is_rejected() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )

    with pytest.raises(ValueError):
        registry.register(
            connection_id="conn-1",
            session_id="sess-2",
            user_id="user-1",
        )


def test_connection_can_be_activated() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )

    connection = registry.activate("conn-1", now=120.0)

    assert connection.state == ConnectionState.ACTIVE
    assert connection.last_heartbeat_at == 120.0


def test_heartbeat_updates_activity_and_revives_stale_connection() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )
    registry.activate("conn-1", now=100.0)

    registry.mark_stale("conn-1")

    heartbeat = registry.heartbeat("conn-1", now=110.0)

    assert heartbeat.state == ConnectionState.ACTIVE
    assert heartbeat.last_heartbeat_at == 110.0


def test_stale_eviction_marks_inactive_connections() -> None:
    clock = FakeClock(100.0)
    registry = make_registry(
        clock,
        stale_after_seconds=30.0,
    )

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )
    registry.activate("conn-1")

    clock.value = 129.0
    assert registry.evict_stale() == []

    clock.value = 130.0
    stale = registry.evict_stale()

    assert len(stale) == 1
    assert stale[0].connection_id == "conn-1"
    assert stale[0].state == ConnectionState.STALE


def test_recent_heartbeat_prevents_stale_eviction() -> None:
    clock = FakeClock(100.0)
    registry = make_registry(
        clock,
        stale_after_seconds=30.0,
    )

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )
    registry.activate("conn-1")

    clock.value = 120.0
    registry.heartbeat("conn-1")

    clock.value = 149.0
    assert registry.evict_stale() == []


def test_disconnect_is_terminal_until_explicit_removal() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )
    registry.activate("conn-1")

    disconnected = registry.disconnect("conn-1")

    assert disconnected.state == ConnectionState.DISCONNECTED

    with pytest.raises(ConnectionStateTransitionError):
        registry.activate("conn-1")

    removed = registry.remove("conn-1")
    assert removed.state == ConnectionState.REMOVED


def test_invalid_registered_to_stale_transition_is_rejected() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )

    with pytest.raises(ConnectionStateTransitionError):
        registry.mark_stale("conn-1")


def test_unknown_connection_is_rejected() -> None:
    registry = make_registry(FakeClock())

    with pytest.raises(ConnectionNotFoundError):
        registry.get("missing")


def test_connection_identity_is_distinct_from_session_identity() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    connection = registry.register(
        connection_id="conn-1",
        session_id="session-transport-42",
        user_id="user-1",
    )

    assert connection.connection_id != connection.session_id


def test_list_is_deterministic() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    for connection_id in ("conn-c", "conn-a", "conn-b"):
        registry.register(
            connection_id=connection_id,
            session_id=f"session-{connection_id}",
            user_id="user-1",
        )

    assert [
        connection.connection_id
        for connection in registry.list()
    ] == [
        "conn-a",
        "conn-b",
        "conn-c",
    ]


def test_only_active_connections_are_evicted() -> None:
    clock = FakeClock(100.0)
    registry = make_registry(clock)

    registry.register(
        connection_id="registered",
        session_id="sess-1",
        user_id="user-1",
    )

    registry.register(
        connection_id="active",
        session_id="sess-2",
        user_id="user-1",
    )
    registry.activate("active")

    registry.register(
        connection_id="disconnected",
        session_id="sess-3",
        user_id="user-1",
    )
    registry.activate("disconnected")
    registry.disconnect("disconnected")

    clock.value = 1000.0
    stale = registry.evict_stale()

    assert [item.connection_id for item in stale] == ["active"]


def test_stale_connection_can_be_reactivated_by_heartbeat() -> None:
    clock = FakeClock(100.0)
    registry = make_registry(
        clock,
        stale_after_seconds=10.0,
    )

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )
    registry.activate("conn-1")

    clock.value = 110.0
    registry.evict_stale()

    assert registry.get("conn-1").state == ConnectionState.STALE

    clock.value = 111.0
    connection = registry.heartbeat("conn-1")

    assert connection.state == ConnectionState.ACTIVE


def test_removed_connection_can_be_re_registered() -> None:
    clock = FakeClock()
    registry = make_registry(clock)

    registry.register(
        connection_id="conn-1",
        session_id="sess-1",
        user_id="user-1",
    )
    registry.remove("conn-1")

    new_connection = registry.register(
        connection_id="conn-1",
        session_id="sess-2",
        user_id="user-1",
    )

    assert new_connection.state == ConnectionState.REGISTERED
    assert new_connection.session_id == "sess-2"