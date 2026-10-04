from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Protocol

from ..session.game_session import (
    CaptureContext,
    GameSession,
    GameSessionError,
    StaleCaptureContextError,
)
from ..session.window_manager import (
    CaptureGeometry,
    GameWindowManager,
    UnsupportedPlatformError,
    WindowTargetError,
)


class FocusGuardError(RuntimeError):
    """Base error for fail-closed GAC-2 target/focus validation."""


class TargetLostError(FocusGuardError):
    """Raised when the GAC-1 binding is no longer current/valid."""


class TargetNotForegroundError(FocusGuardError):
    """Raised when ordinary desktop input would target another foreground HWND."""


class MouseCoordinateError(FocusGuardError):
    """Raised when client-relative mouse coordinates are outside live bounds."""


class ForegroundBackend(Protocol):
    def foreground_hwnd(self) -> int:
        ...


class WindowsForegroundBackend:
    """Lazy Win32 foreground observation used only when concrete input runs."""

    @staticmethod
    def _module():
        if sys.platform != "win32":
            raise UnsupportedPlatformError(
                "Windows foreground validation is available only on win32"
            )
        try:
            import win32gui
        except ImportError as error:  # pragma: no cover - packaging guard
            raise UnsupportedPlatformError(
                "Windows foreground validation requires pywin32"
            ) from error
        return win32gui

    def foreground_hwnd(self) -> int:
        win32gui = self._module()
        return int(win32gui.GetForegroundWindow())


@dataclass(frozen=True, slots=True)
class GuardedTarget:
    context: CaptureContext
    geometry: CaptureGeometry
    screen_x: int | None = None
    screen_y: int | None = None


class FocusGuard:
    """Reuses GAC-1 binding authority before every new input transition."""

    def __init__(
        self,
        session: GameSession,
        window_manager: GameWindowManager,
        backend: ForegroundBackend | None = None,
    ) -> None:
        self._session = session
        self._window_manager = window_manager
        self._backend = backend or WindowsForegroundBackend()

    def validate(
        self,
        *,
        automation_session_id: str,
        binding_generation: int,
        x: int | None = None,
        y: int | None = None,
    ) -> GuardedTarget:
        try:
            context = self._session.current_capture_context()
        except GameSessionError as error:
            raise TargetLostError(str(error)) from error

        if (
            context.automation_session_id != automation_session_id
            or context.binding_generation != binding_generation
        ):
            raise TargetLostError(
                "action session/generation does not match current GAC-1 binding"
            )

        try:
            self._session.assert_current(context)
            geometry = self._window_manager.validate_binding(context.identity)
            self._session.assert_current(context)
        except (GameSessionError, WindowTargetError) as error:
            raise TargetLostError(str(error)) from error

        foreground = self._backend.foreground_hwnd()
        if foreground != context.identity.hwnd:
            raise TargetNotForegroundError(
                "bound game HWND is not the exact foreground target"
            )

        try:
            self._session.assert_current(context)
        except StaleCaptureContextError as error:
            raise TargetLostError(
                "game-session binding changed during focus validation"
            ) from error

        if (x is None) != (y is None):
            raise MouseCoordinateError("x and y must be supplied together")
        if x is None:
            return GuardedTarget(context=context, geometry=geometry)

        if x < 0 or y < 0 or x >= geometry.width or y >= geometry.height:
            raise MouseCoordinateError(
                "client-relative mouse coordinates are outside live target bounds"
            )

        return GuardedTarget(
            context=context,
            geometry=geometry,
            screen_x=geometry.left + x,
            screen_y=geometry.top + y,
        )
