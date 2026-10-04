from dataclasses import dataclass

import pytest

from cl.src.game_automation.capture.frame_source import (
    FrameCaptureUnavailableError,
    NativeFrame,
    StaleFrameError,
)
from cl.src.game_automation.capture.window_capture import (
    WindowCapture,
    WindowsGdiCaptureBackend,
)
from cl.src.game_automation.session.game_session import GameSession
from cl.src.game_automation.session.window_manager import (
    CaptureGeometry,
    GameWindowManager,
)


@dataclass
class _WindowBackend:
    hwnd: int = 100
    pid: int = 200
    started: float = 300.0
    exists: bool = True
    visible: bool = True
    minimized: bool = False

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
        return self.started

    def window_title(self, hwnd):
        return "Game"

    def executable(self, process_id):
        return "game.exe"

    def client_geometry(self, hwnd):
        return CaptureGeometry(left=10, top=20, width=2, height=2)


class _NativeCapture:
    def __init__(self, *, after_capture=None, error=None):
        self.calls = 0
        self.after_capture = after_capture
        self.error = error

    def capture(self, identity, geometry):
        self.calls += 1
        if self.error is not None:
            raise self.error
        frame = NativeFrame(
            width=geometry.width,
            height=geometry.height,
            stride=geometry.width * 4,
            pixel_format="BGRX32_BOTTOM_UP",
            pixels=b"\x01" * (geometry.width * geometry.height * 4),
        )
        if self.after_capture is not None:
            self.after_capture()
        return frame


def _bound_session(window_backend):
    manager = GameWindowManager(window_backend)
    session = GameSession("session-1")
    session.bind(manager.identity_for_hwnd(window_backend.hwnd))
    return manager, session


def test_capture_once_returns_generation_stamped_local_frame():
    window_backend = _WindowBackend()
    manager, session = _bound_session(window_backend)
    native = _NativeCapture()
    capture = WindowCapture(manager, native, monotonic_ns=lambda: 123456)

    first = capture.capture_once(session)
    second = capture.capture_once(session)

    assert first.automation_session_id == "session-1"
    assert first.binding_generation == 1
    assert first.frame_sequence == 1
    assert second.frame_sequence == 2
    assert first.captured_monotonic_ns == 123456
    assert first.pixel_format == "BGRX32_BOTTOM_UP"
    assert first.width == 2
    assert first.height == 2
    assert first.pixels == b"\x01" * 16


def test_pre_capture_validation_failure_performs_no_native_capture():
    window_backend = _WindowBackend()
    manager, session = _bound_session(window_backend)
    window_backend.minimized = True
    native = _NativeCapture()
    capture = WindowCapture(manager, native)

    with pytest.raises(FrameCaptureUnavailableError, match="minimized"):
        capture.capture_once(session)

    assert native.calls == 0


def test_post_capture_native_identity_drift_discards_frame():
    window_backend = _WindowBackend()
    manager, session = _bound_session(window_backend)

    def mutate_target():
        window_backend.pid = 201

    native = _NativeCapture(after_capture=mutate_target)
    capture = WindowCapture(manager, native)

    with pytest.raises(StaleFrameError, match="target changed"):
        capture.capture_once(session)


def test_session_rebind_during_capture_discards_stale_frame():
    window_backend = _WindowBackend()
    manager, session = _bound_session(window_backend)

    def rebind():
        session.bind(manager.identity_for_hwnd(window_backend.hwnd))

    native = _NativeCapture(after_capture=rebind)
    capture = WindowCapture(manager, native)

    with pytest.raises(StaleFrameError, match="session binding changed"):
        capture.capture_once(session)


def test_native_capture_failure_is_local_and_does_not_advance_sequence():
    window_backend = _WindowBackend()
    manager, session = _bound_session(window_backend)
    native = _NativeCapture(error=FrameCaptureUnavailableError("native failed"))
    capture = WindowCapture(manager, native)

    with pytest.raises(FrameCaptureUnavailableError, match="native failed"):
        capture.capture_once(session)

    context = session.current_capture_context()
    assert session.reserve_frame_sequence(context) == 1


class _FakeBitmap:
    def __init__(self, events):
        self.events = events

    def CreateCompatibleBitmap(self, source_dc, width, height):
        self.events.append(("create_bitmap", width, height))

    def GetHandle(self):
        return 999


class _FakeMemoryDC:
    def __init__(self, events):
        self.events = events

    def SelectObject(self, bitmap):
        self.events.append("select_bitmap")

    def BitBlt(self, *args):
        self.events.append("bitblt")
        raise RuntimeError("injected native failure")

    def DeleteDC(self):
        self.events.append("delete_memory_dc")


class _FakeSourceDC:
    def __init__(self, events):
        self.events = events

    def CreateCompatibleDC(self):
        self.events.append("create_memory_dc")
        return _FakeMemoryDC(self.events)

    def DeleteDC(self):
        self.events.append("delete_source_dc")


class _FakeWin32UI:
    def __init__(self, events):
        self.events = events

    def CreateDCFromHandle(self, handle):
        self.events.append(("create_source_dc", handle))
        return _FakeSourceDC(self.events)

    def CreateBitmap(self):
        self.events.append("create_bitmap_object")
        return _FakeBitmap(self.events)


class _FakeWin32GUI:
    def __init__(self, events):
        self.events = events

    def GetWindowDC(self, hwnd):
        self.events.append(("get_window_dc", hwnd))
        return 123

    def DeleteObject(self, handle):
        self.events.append(("delete_object", handle))

    def ReleaseDC(self, hwnd, handle):
        self.events.append(("release_dc", hwnd, handle))


class _FakeWin32Con:
    SRCCOPY = 0x00CC0020


class _InjectedGdiBackend(WindowsGdiCaptureBackend):
    def __init__(self, events):
        self.events = events

    def _modules(self):
        return (
            _FakeWin32Con(),
            _FakeWin32GUI(self.events),
            _FakeWin32UI(self.events),
        )


def test_native_gdi_resources_are_released_on_capture_failure():
    events = []
    backend = _InjectedGdiBackend(events)

    with pytest.raises(FrameCaptureUnavailableError, match="native GDI capture failed"):
        backend.capture(
            identity=GameWindowManager(_WindowBackend()).identity_for_hwnd(100),
            geometry=CaptureGeometry(left=0, top=0, width=2, height=2),
        )

    assert "delete_memory_dc" in events
    assert "delete_source_dc" in events
    assert ("delete_object", 999) in events
    assert ("release_dc", 0, 123) in events
