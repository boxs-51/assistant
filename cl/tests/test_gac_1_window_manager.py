from dataclasses import dataclass

import pytest

from cl.src.game_automation.session.game_session import GameWindowIdentity
from cl.src.game_automation.session.window_manager import (
    CaptureGeometry,
    GameWindowManager,
    WindowTargetUnavailableError,
)


class _BackendSpecificError(Exception):
    pass


@dataclass
class _FakeBackend:
    hwnd: int = 100
    pid: int = 200
    started: float = 300.0
    title: str = "Game"
    executable_path: str = "game.exe"
    exists: bool = True
    visible: bool = True
    minimized: bool = False
    geometry_error: Exception | None = None
    process_start_error: Exception | None = None

    def enumerate_windows(self):
        return (self.hwnd,)

    def is_window(self, hwnd):
        return self.exists and hwnd == self.hwnd

    def is_visible(self, hwnd):
        return self.visible

    def is_minimized(self, hwnd):
        return self.minimized

    def process_id(self, hwnd):
        return self.pid

    def process_start_time(self, process_id):
        if self.process_start_error is not None:
            raise self.process_start_error
        return self.started

    def window_title(self, hwnd):
        return self.title

    def executable(self, process_id):
        return self.executable_path

    def client_geometry(self, hwnd):
        if self.geometry_error is not None:
            raise self.geometry_error
        return CaptureGeometry(left=10, top=20, width=640, height=480)


def test_enumeration_builds_exact_native_identity():
    backend = _FakeBackend()
    manager = GameWindowManager(backend)

    identities = manager.enumerate_windows()

    assert identities == (
        GameWindowIdentity(
            hwnd=100,
            process_id=200,
            process_start_time=300.0,
            title="Game",
            executable="game.exe",
        ),
    )


def test_same_title_does_not_allow_hwnd_or_pid_retargeting():
    backend = _FakeBackend()
    manager = GameWindowManager(backend)
    identity = manager.identity_for_hwnd(backend.hwnd)

    backend.pid = 201
    backend.title = identity.title

    with pytest.raises(WindowTargetUnavailableError, match="process changed"):
        manager.validate_binding(identity)


def test_pid_reuse_with_changed_process_start_time_fails_closed():
    backend = _FakeBackend()
    manager = GameWindowManager(backend)
    identity = manager.identity_for_hwnd(backend.hwnd)

    backend.started = 301.0

    with pytest.raises(WindowTargetUnavailableError, match="start time changed"):
        manager.validate_binding(identity)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"exists": False}, "destroyed or replaced"),
        ({"visible": False}, "not visible"),
        ({"minimized": True}, "minimized"),
    ],
)
def test_unsupported_or_lost_target_state_fails_closed(mutation, expected):
    backend = _FakeBackend()
    manager = GameWindowManager(backend)
    identity = manager.identity_for_hwnd(backend.hwnd)

    for key, value in mutation.items():
        setattr(backend, key, value)

    with pytest.raises(WindowTargetUnavailableError, match=expected):
        manager.validate_binding(identity)


def test_invalid_or_zero_capture_area_fails_closed():
    backend = _FakeBackend()
    manager = GameWindowManager(backend)
    identity = manager.identity_for_hwnd(backend.hwnd)

    backend.geometry_error = ValueError("capture geometry must have a positive area")

    with pytest.raises(WindowTargetUnavailableError, match="capture area"):
        manager.validate_binding(identity)


def test_unstable_discovery_candidate_is_omitted_not_authoritative():
    backend = _FakeBackend(geometry_error=OSError("window disappeared"))
    manager = GameWindowManager(backend)

    assert manager.enumerate_windows() == ()


def test_backend_specific_process_race_is_normalized_to_structured_failure():
    backend = _FakeBackend()
    manager = GameWindowManager(backend)
    identity = manager.identity_for_hwnd(backend.hwnd)

    backend.process_start_error = _BackendSpecificError("process vanished")

    with pytest.raises(WindowTargetUnavailableError, match="validation failed closed"):
        manager.validate_binding(identity)


def test_backend_specific_identity_resolution_race_is_structured():
    backend = _FakeBackend(
        process_start_error=_BackendSpecificError("process vanished")
    )
    manager = GameWindowManager(backend)

    with pytest.raises(
        WindowTargetUnavailableError,
        match="process identity cannot be resolved",
    ):
        manager.identity_for_hwnd(backend.hwnd)
