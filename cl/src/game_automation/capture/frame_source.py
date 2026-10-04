from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..session.game_session import GameSession


class FrameCaptureError(RuntimeError):
    """Base error for bounded GAC-1 capture failures."""


class FrameCaptureUnavailableError(FrameCaptureError):
    """Raised when the selected target cannot safely produce a frame."""


class StaleFrameError(FrameCaptureError):
    """Raised when native capture completes after the binding became stale."""


@dataclass(frozen=True, slots=True)
class NativeFrame:
    """Raw local pixel buffer returned by an injectable native capture seam."""

    width: int
    height: int
    stride: int
    pixel_format: str
    pixels: bytes

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("native frame dimensions must be positive")
        if self.stride <= 0:
            raise ValueError("native frame stride must be positive")
        if not self.pixel_format:
            raise ValueError("native frame pixel_format is required")
        if len(self.pixels) < self.stride * self.height:
            raise ValueError("native frame payload is shorter than stride * height")


@dataclass(frozen=True, slots=True)
class FrameObservation:
    """Immutable, process-local frame evidence for one binding generation."""

    automation_session_id: str
    binding_generation: int
    frame_sequence: int
    captured_monotonic_ns: int
    width: int
    height: int
    stride: int
    pixel_format: str
    pixels: bytes

    def __post_init__(self) -> None:
        if not self.automation_session_id:
            raise ValueError("automation_session_id is required")
        if self.binding_generation <= 0:
            raise ValueError("binding_generation must be positive")
        if self.frame_sequence <= 0:
            raise ValueError("frame_sequence must be positive")
        if self.captured_monotonic_ns <= 0:
            raise ValueError("captured_monotonic_ns must be positive")
        if self.width <= 0 or self.height <= 0 or self.stride <= 0:
            raise ValueError("frame dimensions and stride must be positive")
        if not self.pixel_format:
            raise ValueError("pixel_format is required")
        if len(self.pixels) < self.stride * self.height:
            raise ValueError("frame payload is shorter than stride * height")


class FrameSource(Protocol):
    """Synchronous one-frame capture boundary.

    GAC-1 intentionally does not define a background capture worker or queue.
    Later stages may compose this primitive behind separately claimed runtime
    authority.
    """

    def capture_once(self, session: GameSession) -> FrameObservation:
        ...
