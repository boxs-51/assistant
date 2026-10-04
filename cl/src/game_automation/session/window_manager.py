from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Iterable, Protocol

from .game_session import GameWindowIdentity


class WindowTargetError(RuntimeError):
    """Base error for fail-closed native target validation."""


class UnsupportedPlatformError(WindowTargetError):
    """Raised when the concrete Windows backend is used off Windows."""


class WindowTargetUnavailableError(WindowTargetError):
    """Raised when a bound window is no longer safely capturable."""


@dataclass(frozen=True, slots=True)
class CaptureGeometry:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("capture geometry must have a positive area")


class WindowNativeBackend(Protocol):
    """Injectable platform seam used by deterministic GAC-1 tests."""

    def enumerate_windows(self) -> Iterable[int]:
        ...

    def is_window(self, hwnd: int) -> bool:
        ...

    def is_visible(self, hwnd: int) -> bool:
        ...

    def is_minimized(self, hwnd: int) -> bool:
        ...

    def process_id(self, hwnd: int) -> int:
        ...

    def process_start_time(self, process_id: int) -> float:
        ...

    def window_title(self, hwnd: int) -> str:
        ...

    def executable(self, process_id: int) -> str | None:
        ...

    def client_geometry(self, hwnd: int) -> CaptureGeometry:
        ...


class WindowsWindowBackend:
    """Minimal pywin32/psutil adapter loaded only when actually used."""

    @staticmethod
    def _modules():
        if sys.platform != "win32":
            raise UnsupportedPlatformError(
                "Windows game-window discovery is available only on win32"
            )
        try:
            import psutil
            import win32gui
            import win32process
        except ImportError as error:  # pragma: no cover - Windows packaging guard
            raise UnsupportedPlatformError(
                "Windows game-window discovery requires pywin32 and psutil"
            ) from error
        return win32gui, win32process, psutil

    def enumerate_windows(self) -> tuple[int, ...]:
        win32gui, _win32process, _psutil = self._modules()
        handles: list[int] = []

        def callback(hwnd, _extra):
            handles.append(int(hwnd))
            return True

        win32gui.EnumWindows(callback, None)
        return tuple(handles)

    def is_window(self, hwnd: int) -> bool:
        win32gui, _win32process, _psutil = self._modules()
        return bool(win32gui.IsWindow(hwnd))

    def is_visible(self, hwnd: int) -> bool:
        win32gui, _win32process, _psutil = self._modules()
        return bool(win32gui.IsWindowVisible(hwnd))

    def is_minimized(self, hwnd: int) -> bool:
        win32gui, _win32process, _psutil = self._modules()
        return bool(win32gui.IsIconic(hwnd))

    def process_id(self, hwnd: int) -> int:
        _win32gui, win32process, _psutil = self._modules()
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        return int(process_id)

    def process_start_time(self, process_id: int) -> float:
        _win32gui, _win32process, psutil = self._modules()
        return float(psutil.Process(process_id).create_time())

    def window_title(self, hwnd: int) -> str:
        win32gui, _win32process, _psutil = self._modules()
        return str(win32gui.GetWindowText(hwnd) or "")

    def executable(self, process_id: int) -> str | None:
        _win32gui, _win32process, psutil = self._modules()
        try:
            return str(psutil.Process(process_id).exe())
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return None

    def client_geometry(self, hwnd: int) -> CaptureGeometry:
        win32gui, _win32process, _psutil = self._modules()
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        origin_x, origin_y = win32gui.ClientToScreen(hwnd, (left, top))
        return CaptureGeometry(
            left=int(origin_x),
            top=int(origin_y),
            width=int(right - left),
            height=int(bottom - top),
        )


class GameWindowManager:
    """Discovers windows and validates one exact native binding fail-closed."""

    def __init__(self, backend: WindowNativeBackend | None = None) -> None:
        self._backend = backend or WindowsWindowBackend()

    def enumerate_windows(self) -> tuple[GameWindowIdentity, ...]:
        try:
            handles = tuple(self._backend.enumerate_windows())
        except UnsupportedPlatformError:
            raise
        except Exception as error:
            raise WindowTargetUnavailableError(
                "native window enumeration failed"
            ) from error

        candidates: list[GameWindowIdentity] = []
        for hwnd in handles:
            try:
                if not self._backend.is_window(hwnd):
                    continue
                if not self._backend.is_visible(hwnd):
                    continue
                if self._backend.is_minimized(hwnd):
                    continue
                geometry = self._backend.client_geometry(hwnd)
                if geometry.width <= 0 or geometry.height <= 0:
                    continue
                candidates.append(self.identity_for_hwnd(hwnd))
            except UnsupportedPlatformError:
                raise
            except Exception:
                # Enumeration is discovery only; an unstable candidate is omitted
                # rather than becoming binding authority.
                continue
        return tuple(candidates)

    def identity_for_hwnd(self, hwnd: int) -> GameWindowIdentity:
        try:
            if not self._backend.is_window(hwnd):
                raise WindowTargetUnavailableError("native window no longer exists")

            process_id = int(self._backend.process_id(hwnd))
            if process_id <= 0:
                raise WindowTargetUnavailableError(
                    "window has no valid process owner"
                )

            process_start_time = float(
                self._backend.process_start_time(process_id)
            )
            title = str(self._backend.window_title(hwnd) or "")
            executable = self._backend.executable(process_id)

            return GameWindowIdentity(
                hwnd=int(hwnd),
                process_id=process_id,
                process_start_time=process_start_time,
                title=title,
                executable=executable,
            )
        except UnsupportedPlatformError:
            raise
        except WindowTargetUnavailableError:
            raise
        except Exception as error:
            raise WindowTargetUnavailableError(
                "window process identity cannot be resolved"
            ) from error

    def validate_binding(self, identity: GameWindowIdentity) -> CaptureGeometry:
        """Validate HWND/PID/process-instance and capture-supported state."""

        hwnd = identity.hwnd
        try:
            if not self._backend.is_window(hwnd):
                raise WindowTargetUnavailableError(
                    "bound window was destroyed or replaced"
                )
            if not self._backend.is_visible(hwnd):
                raise WindowTargetUnavailableError("bound window is not visible")
            if self._backend.is_minimized(hwnd):
                raise WindowTargetUnavailableError(
                    "minimized windows are not capturable"
                )

            current_pid = int(self._backend.process_id(hwnd))
            if current_pid != identity.process_id:
                raise WindowTargetUnavailableError(
                    "bound window process changed; refusing HWND reuse"
                )

            current_start_time = float(
                self._backend.process_start_time(current_pid)
            )
            if current_start_time != identity.process_start_time:
                raise WindowTargetUnavailableError(
                    "bound process start time changed; refusing PID reuse"
                )

            try:
                geometry = self._backend.client_geometry(hwnd)
            except Exception as error:
                raise WindowTargetUnavailableError(
                    "bound window has no valid client capture area"
                ) from error

            if geometry.width <= 0 or geometry.height <= 0:
                raise WindowTargetUnavailableError(
                    "bound window has an empty client capture area"
                )
            return geometry
        except UnsupportedPlatformError:
            raise
        except WindowTargetUnavailableError:
            raise
        except Exception as error:
            raise WindowTargetUnavailableError(
                "bound window validation failed closed"
            ) from error
