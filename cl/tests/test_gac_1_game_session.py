import threading

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


def test_explicit_empty_session_id_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        GameSession("")


def test_omitted_session_id_generates_local_identity():
    session = GameSession()
    assert session.automation_session_id


def test_hold_current_accepts_current_context_without_exposing_lock():
    session = GameSession("guard-current")
    session.bind(_identity())
    context = session.current_capture_context()

    guard = session.hold_current(context)
    assert not hasattr(guard, "acquire")
    assert not hasattr(guard, "release")

    with guard as lease:
        assert lease is None
        session.assert_current(context)
        assert session.is_current(context)


@pytest.mark.parametrize("mutation", ["rebind", "unbind", "close"])
def test_hold_current_rejects_context_invalidated_before_entry(mutation):
    session = GameSession(f"guard-stale-{mutation}")
    session.bind(_identity())
    stale = session.current_capture_context()

    if mutation == "rebind":
        session.bind(_identity(hwnd=101, pid=201, started=301.0))
    elif mutation == "unbind":
        session.unbind()
    else:
        session.close()

    with pytest.raises(StaleCaptureContextError):
        with session.hold_current(stale):
            raise AssertionError("stale lifecycle guard must not yield")


@pytest.mark.parametrize("mutation", ["rebind", "unbind", "close"])
def test_hold_current_blocks_concurrent_lifecycle_mutation_until_exit(mutation):
    session = GameSession(f"guard-block-{mutation}")
    session.bind(_identity())
    context = session.current_capture_context()

    attempted = threading.Event()
    completed = threading.Event()
    failures = []

    def mutate():
        attempted.set()
        try:
            if mutation == "rebind":
                session.bind(_identity(hwnd=102, pid=202, started=302.0))
            elif mutation == "unbind":
                session.unbind()
            else:
                session.close()
        except BaseException as error:
            failures.append(error)
        finally:
            completed.set()

    worker = threading.Thread(target=mutate, daemon=True)

    with session.hold_current(context):
        worker.start()
        assert attempted.wait(timeout=0.5)
        assert completed.wait(timeout=0.05) is False
        assert session.is_current(context)

    assert completed.wait(timeout=0.5)
    worker.join(timeout=0.5)
    assert worker.is_alive() is False
    assert failures == []
    assert session.is_current(context) is False


def test_hold_current_releases_lifecycle_lock_when_guarded_body_raises():
    session = GameSession("guard-exception")
    session.bind(_identity())
    context = session.current_capture_context()

    with pytest.raises(RuntimeError, match="boom"):
        with session.hold_current(context):
            raise RuntimeError("boom")

    completed = threading.Event()
    failures = []

    def rebind():
        try:
            session.bind(_identity(hwnd=103, pid=203, started=303.0))
        except BaseException as error:
            failures.append(error)
        finally:
            completed.set()

    worker = threading.Thread(target=rebind, daemon=True)
    worker.start()

    assert completed.wait(timeout=0.5)
    worker.join(timeout=0.5)
    assert worker.is_alive() is False
    assert failures == []
    assert session.binding_generation == 2
