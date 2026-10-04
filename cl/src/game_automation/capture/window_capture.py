from __future__ import annotations

import sys
import time
from typing import Protocol

from .frame_source import (
    FrameCaptureUnavailableError,
    FrameObservation,
    NativeFrame,
    StaleFrameError,
)
from ..session.game_session import (
    GameSession,
    GameWindowIdentity,
    StaleCaptureContextError,
)
from ..session.window_manager import (
    CaptureGeometry,
    GameWindowManager,
    UnsupportedPlatformError,
    WindowTargetError,
)


class NativeCaptureBackend(Protocol):
    """Injectable native capture seam used by GAC-1 tests."""

    def capture(
        self,
        identity: GameWindowIdentity,
        geometry: CaptureGeometry,
    ) -> NativeFrame:
        ...


class WindowsGdiCaptureBackend:
    """Visible-window baseline using a screen DC and pywin32 GDI.

    This is deliberately not an exclusive-fullscreen, protected-surface, DXGI,
    or Windows Graphics Capture implementation.
    """

    @staticmethod
    def _modules():
        if sys.platform != "win32":
            raise UnsupportedPlatformError(
                "Windows GDI game capture is available only on win32"
            )
        try:
            import win32con
            import win32gui
            import win32ui
        except ImportError as error:  # pragma: no cover - Windows packaging guard
            raise UnsupportedPlatformError(
                "Windows GDI game capture requires pywin32"
            ) from error
        return win32con, win32gui, win32ui

    def capture(
        self,
        identity: GameWindowIdentity,
        geometry: CaptureGeometry,
    ) -> NativeFrame:
        win32con, win32gui, win32ui = self._modules()

        screen_dc_handle = None
        source_dc = None
        memory_dc = None
        bitmap = None

        try:
            # Capture from the visible desktop at the validated client-area
            # coordinates. This baseline intentionally fails closed for targets
            # that cannot be represented by ordinary visible GDI pixels.
            screen_dc_handle = win32gui.GetWindowDC(0)
            if not screen_dc_handle:
                raise FrameCaptureUnavailableError("failed to acquire screen DC")

            source_dc = win32ui.CreateDCFromHandle(screen_dc_handle)
            memory_dc = source_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(
                source_dc,
                geometry.width,
                geometry.height,
            )
            memory_dc.SelectObject(bitmap)
            memory_dc.BitBlt(
                (0, 0),
                (geometry.width, geometry.height),
                source_dc,
                (geometry.left, geometry.top),
                win32con.SRCCOPY,
            )

            info = bitmap.GetInfo()
            bits_per_pixel = int(info.get("bmBitsPixel", 0))
            stride = int(info.get("bmWidthBytes", 0))
            if bits_per_pixel != 32 or stride <= 0:
                raise FrameCaptureUnavailableError(
                    "GAC-1 GDI baseline requires a 32-bit compatible bitmap"
                )

            pixels = bytes(bitmap.GetBitmapBits(True))
            return NativeFrame(
                width=geometry.width,
                height=geometry.height,
                stride=stride,
                pixel_format="BGRX32_BOTTOM_UP",
                pixels=pixels,
            )
        except FrameCaptureUnavailableError:
            raise
        except Exception as error:
            raise FrameCaptureUnavailableError(
                f"native GDI capture failed for hwnd={identity.hwnd}"
            ) from error
        finally:
            # pywin32 objects are intentionally cleaned in every fault path.
            if memory_dc is not None:
                try:
                    memory_dc.DeleteDC()
                except Exception:
                    pass
            if source_dc is not None:
                try:
                    source_dc.DeleteDC()
                except Exception:
                    pass
            if bitmap is not None:
                try:
                    win32gui.DeleteObject(bitmap.GetHandle())
                except Exception:
                    pass
            if screen_dc_handle is not None:
                try:
                    win32gui.ReleaseDC(0, screen_dc_handle)
                except Exception:
                    pass


class WindowCapture:
    """Synchronous capture-one-frame implementation with double fencing."""

    def __init__(
        self,
        window_manager: GameWindowManager,
        backend: NativeCaptureBackend | None = None,
        *,
        monotonic_ns=time.monotonic_ns,
    ) -> None:
        self._window_manager = window_manager
        self._backend = backend or WindowsGdiCaptureBackend()
        self._monotonic_ns = monotonic_ns

    def capture_once(self, session: GameSession) -> FrameObservation:
        context = session.current_capture_context()

        try:
            geometry = self._window_manager.validate_binding(context.identity)
        except WindowTargetError as error:
            raise FrameCaptureUnavailableError(str(error)) from error

        native = self._backend.capture(context.identity, geometry)

        # Native capture may block long enough for the HWND/PID/session binding
        # to change. Validate the native target again before accepting pixels.
        try:
            self._window_manager.validate_binding(context.identity)
        except WindowTargetError as error:
            raise StaleFrameError(
                "bound target changed while native capture was in progress"
            ) from error

        try:
            session.assert_current(context)
            frame_sequence = session.reserve_frame_sequence(context)
        except StaleCaptureContextError as error:
            raise StaleFrameError(
                "game-session binding changed while capture was in progress"
            ) from error

        return FrameObservation(
            automation_session_id=context.automation_session_id,
            binding_generation=context.binding_generation,
            frame_sequence=frame_sequence,
            captured_monotonic_ns=int(self._monotonic_ns()),
            width=native.width,
            height=native.height,
            stride=native.stride,
            pixel_format=native.pixel_format,
            pixels=native.pixels,
        )
