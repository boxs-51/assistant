from __future__ import annotations

import sys
import threading
from typing import Protocol

from ..session.window_manager import UnsupportedPlatformError


class KeyboardBackend(Protocol):
    def key_down(self, key: str) -> None:
        ...

    def key_up(self, key: str) -> None:
        ...


class WindowsKeyboardBackend:
    """Ordinary desktop keyboard backend loaded lazily on Windows."""

    @staticmethod
    def _module():
        if sys.platform != "win32":
            raise UnsupportedPlatformError(
                "Windows keyboard input is available only on win32"
            )
        try:
            import pyautogui
        except ImportError as error:  # pragma: no cover - packaging guard
            raise UnsupportedPlatformError(
                "Windows keyboard input requires PyAutoGUI"
            ) from error
        return pyautogui

    def key_down(self, key: str) -> None:
        self._module().keyDown(key)

    def key_up(self, key: str) -> None:
        self._module().keyUp(key)


class KeyboardExecutor:
    """Tracks only keys placed down by this GAC-2 executor."""

    def __init__(self, backend: KeyboardBackend | None = None) -> None:
        self._backend = backend or WindowsKeyboardBackend()
        self._lock = threading.RLock()
        self._owned_keys: set[str] = set()

    @property
    def owned_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._owned_keys)

    def key_down(self, key: str) -> None:
        if not key:
            raise ValueError("key must not be empty")
        with self._lock:
            if key in self._owned_keys:
                raise RuntimeError("key is already owned/down by GAC-2")
            self._backend.key_down(key)
            self._owned_keys.add(key)

    def key_up(self, key: str) -> bool:
        with self._lock:
            if key not in self._owned_keys:
                return False
            self._backend.key_up(key)
            self._owned_keys.remove(key)
            return True

    def cleanup(self) -> tuple[str, ...]:
        """Best-effort release of GAC-owned keys only."""

        failures: list[str] = []
        with self._lock:
            for key in tuple(self._owned_keys):
                try:
                    self._backend.key_up(key)
                except Exception:
                    failures.append(key)
                else:
                    self._owned_keys.remove(key)
        return tuple(failures)
