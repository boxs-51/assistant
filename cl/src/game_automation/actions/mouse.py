from __future__ import annotations

import sys
import threading
from typing import Protocol

from ..session.window_manager import UnsupportedPlatformError


class MouseBackend(Protocol):
    def move_to(self, x: int, y: int) -> None:
        ...

    def button_down(self, button: str) -> None:
        ...

    def button_up(self, button: str) -> None:
        ...


class WindowsMouseBackend:
    """Ordinary desktop mouse backend loaded lazily on Windows."""

    @staticmethod
    def _module():
        if sys.platform != "win32":
            raise UnsupportedPlatformError(
                "Windows mouse input is available only on win32"
            )
        try:
            import pyautogui
        except ImportError as error:  # pragma: no cover - packaging guard
            raise UnsupportedPlatformError(
                "Windows mouse input requires PyAutoGUI"
            ) from error
        return pyautogui

    def move_to(self, x: int, y: int) -> None:
        self._module().moveTo(x, y)

    def button_down(self, button: str) -> None:
        self._module().mouseDown(button=button)

    def button_up(self, button: str) -> None:
        self._module().mouseUp(button=button)


class MouseExecutor:
    """Tracks only mouse buttons placed down by this GAC-2 executor."""

    def __init__(self, backend: MouseBackend | None = None) -> None:
        self._backend = backend or WindowsMouseBackend()
        self._lock = threading.RLock()
        self._owned_buttons: set[str] = set()

    @property
    def owned_buttons(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._owned_buttons)

    def move_to(self, x: int, y: int) -> None:
        self._backend.move_to(x, y)

    def button_down(self, button: str) -> None:
        if not button:
            raise ValueError("button must not be empty")
        with self._lock:
            if button in self._owned_buttons:
                raise RuntimeError("mouse button is already owned/down by GAC-2")
            self._backend.button_down(button)
            self._owned_buttons.add(button)

    def button_down_at(self, button: str, x: int, y: int) -> None:
        """Place a button down at coordinates from the same live guard.

        Moving immediately before button-down closes the stale-coordinate gap
        between a prior guarded move and the guarded down transition. External
        focus theft between ordinary desktop API calls remains the explicitly
        documented GAC-2 platform limitation.
        """

        if not button:
            raise ValueError("button must not be empty")
        with self._lock:
            if button in self._owned_buttons:
                raise RuntimeError("mouse button is already owned/down by GAC-2")
            self._backend.move_to(x, y)
            self._backend.button_down(button)
            self._owned_buttons.add(button)

    def button_up(self, button: str) -> bool:
        with self._lock:
            if button not in self._owned_buttons:
                return False
            self._backend.button_up(button)
            self._owned_buttons.remove(button)
            return True

    def cleanup(self) -> tuple[str, ...]:
        """Best-effort release of GAC-owned mouse buttons only."""

        failures: list[str] = []
        with self._lock:
            for button in tuple(self._owned_buttons):
                try:
                    self._backend.button_up(button)
                except Exception:
                    failures.append(button)
                else:
                    self._owned_buttons.remove(button)
        return tuple(failures)
