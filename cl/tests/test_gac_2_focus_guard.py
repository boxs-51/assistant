import pytest

from cl.src.game_automation.actions.focus_guard import (
    FocusGuard,
    MouseCoordinateError,
    TargetLostError,
    TargetNotForegroundError,
    WindowsForegroundBackend,
)
from cl.src.game_automation.session.game_session import (
    GameSession,
    GameWindowIdentity,
)
from cl.src.game_automation.session.window_manager import (
    CaptureGeometry,
    UnsupportedPlatformError,
    WindowTargetUnavailableError,
)


class FakeWindowManager:
    def __init__(self):
        self.geometry = CaptureGeometry(left=100, top=200, width=640, height=480)
        self.fail = False

    def validate_binding(self, _identity):
        if self.fail:
            raise WindowTargetUnavailableError("identity changed")
        return self.geometry


class FakeForeground:
    def __init__(self, hwnd):
        self.hwnd = hwnd

    def foreground_hwnd(self):
        return self.hwnd


@pytest.fixture
def target():
    session = GameSession("focus-session")
    identity = GameWindowIdentity(
        hwnd=101,
        process_id=202,
        process_start_time=303.0,
    )
    binding = session.bind(identity)
    manager = FakeWindowManager()
    foreground = FakeForeground(identity.hwnd)
    guard = FocusGuard(session, manager, foreground)
    return session, identity, binding, manager, foreground, guard


def test_exact_bound_foreground_target_and_client_relative_conversion(target):
    session, identity, binding, manager, _foreground, guard = target

    result = guard.validate(
        automation_session_id=session.automation_session_id,
        binding_generation=binding.generation,
        x=10,
        y=20,
    )

    assert result.context.identity == identity
    assert result.geometry == manager.geometry
    assert (result.screen_x, result.screen_y) == (110, 220)


def test_wrong_foreground_fails_closed(target):
    session, _identity, binding, _manager, foreground, guard = target
    foreground.hwnd = 999

    with pytest.raises(TargetNotForegroundError):
        guard.validate(
            automation_session_id=session.automation_session_id,
            binding_generation=binding.generation,
        )


def test_gac1_identity_validation_failure_fails_closed(target):
    session, _identity, binding, manager, _foreground, guard = target
    manager.fail = True

    with pytest.raises(TargetLostError):
        guard.validate(
            automation_session_id=session.automation_session_id,
            binding_generation=binding.generation,
        )


def test_binding_generation_drift_fails_closed(target):
    session, identity, old_binding, _manager, _foreground, guard = target
    session.bind(identity)

    with pytest.raises(TargetLostError):
        guard.validate(
            automation_session_id=session.automation_session_id,
            binding_generation=old_binding.generation,
        )


def test_unbound_and_closed_sessions_fail_closed(target):
    session, _identity, binding, _manager, _foreground, guard = target
    session.unbind()
    with pytest.raises(TargetLostError):
        guard.validate(
            automation_session_id=session.automation_session_id,
            binding_generation=binding.generation,
        )

    closed = GameSession("closed")
    closed_identity = GameWindowIdentity(
        hwnd=1,
        process_id=2,
        process_start_time=3.0,
    )
    closed_binding = closed.bind(closed_identity)
    closed.close()
    closed_guard = FocusGuard(
        closed,
        FakeWindowManager(),
        FakeForeground(closed_identity.hwnd),
    )
    with pytest.raises(TargetLostError):
        closed_guard.validate(
            automation_session_id=closed.automation_session_id,
            binding_generation=closed_binding.generation,
        )


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (640, 0), (0, 480)])
def test_mouse_coordinates_outside_live_client_rect_fail_closed(
    target,
    x,
    y,
):
    session, _identity, binding, _manager, _foreground, guard = target

    with pytest.raises(MouseCoordinateError):
        guard.validate(
            automation_session_id=session.automation_session_id,
            binding_generation=binding.generation,
            x=x,
            y=y,
        )


def test_concrete_focus_backend_fails_explicitly_off_windows(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    with pytest.raises(UnsupportedPlatformError, match="only on win32"):
        WindowsForegroundBackend().foreground_hwnd()
