import pytest

from cl.src.game_automation.session.game_session import (
    GameSession,
    GameSessionState,
    GameWindowIdentity,
    SessionClosedError,
    StaleCaptureContextError,
    UnboundSessionError,
)


def _identity(hwnd=100, pid=200, started=300.0, title="Game"):
    return GameWindowIdentity(
        hwnd=hwnd,
        process_id=pid,
        process_start_time=started,
        title=title,
        executable="game.exe",
    )


def test_bind_rebind_unbind_fences_prior_contexts():
    session = GameSession("session-1")
    assert session.state is GameSessionState.UNBOUND

    first = session.bind(_identity())
    first_context = session.current_capture_context()
    assert first.generation == 1
    assert first_context.binding_generation == 1
    assert session.is_current(first_context)

    second = session.bind(_identity(hwnd=101))
    assert second.generation == 2
    assert not session.is_current(first_context)
    with pytest.raises(StaleCaptureContextError):
        session.assert_current(first_context)

    second_context = session.current_capture_context()
    invalidated_generation = session.unbind()
    assert invalidated_generation == 3
    assert session.state is GameSessionState.UNBOUND
    assert not session.is_current(second_context)
    with pytest.raises(UnboundSessionError):
        session.current_capture_context()


def test_frame_sequence_is_monotonic_inside_one_binding_and_resets_on_rebind():
    session = GameSession("session-1")
    session.bind(_identity())
    context = session.current_capture_context()

    assert session.reserve_frame_sequence(context) == 1
    assert session.reserve_frame_sequence(context) == 2
    assert session.reserve_frame_sequence(context) == 3

    session.bind(_identity(hwnd=101, pid=201, started=301.0))
    new_context = session.current_capture_context()
    assert new_context.binding_generation == 2
    assert session.reserve_frame_sequence(new_context) == 1

    with pytest.raises(StaleCaptureContextError):
        session.reserve_frame_sequence(context)


def test_close_is_terminal_and_invalidates_inflight_context():
    session = GameSession("session-1")
    session.bind(_identity())
    context = session.current_capture_context()

    closed_generation = session.close()
    assert closed_generation == 2
    assert session.state is GameSessionState.CLOSED
    assert not session.is_current(context)

    # Idempotent close does not manufacture repeated generations.
    assert session.close() == closed_generation

    with pytest.raises(SessionClosedError):
        session.current_capture_context()
    with pytest.raises(SessionClosedError):
        session.bind(_identity(hwnd=102))
    with pytest.raises(SessionClosedError):
        session.unbind()


def test_window_title_is_metadata_not_binding_authority():
    left = _identity(hwnd=100, pid=200, started=300.0, title="Same Game")
    right = _identity(hwnd=101, pid=201, started=301.0, title="Same Game")

    assert left.title == right.title
    assert left != right
