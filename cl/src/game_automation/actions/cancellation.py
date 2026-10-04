from __future__ import annotations

from enum import Enum
import math
import threading
import time


class WaitResult(str, Enum):
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"


class WakeSignal:
    """Shared local condition used to wake pacing/hold waits promptly."""

    def __init__(self) -> None:
        self.condition = threading.Condition()


class CancellationToken:
    """Single-action cancellation state; a new action receives a new token."""

    def __init__(self, signal: WakeSignal | None = None) -> None:
        self._signal = signal or WakeSignal()
        self._cancelled = False

    @property
    def signal(self) -> WakeSignal:
        return self._signal

    @property
    def is_cancelled(self) -> bool:
        with self._signal.condition:
            return self._cancelled

    def cancel(self) -> bool:
        with self._signal.condition:
            if self._cancelled:
                return False
            self._cancelled = True
            self._signal.condition.notify_all()
            return True


class EmergencyStopLatch:
    """Process-local terminal stop latch with no reset/re-arm operation."""

    def __init__(self, signal: WakeSignal | None = None) -> None:
        self._signal = signal or WakeSignal()
        self._triggered = False

    @property
    def signal(self) -> WakeSignal:
        return self._signal

    @property
    def is_triggered(self) -> bool:
        with self._signal.condition:
            return self._triggered

    def trigger(self) -> bool:
        with self._signal.condition:
            if self._triggered:
                return False
            self._triggered = True
            self._signal.condition.notify_all()
            return True


def wait_interruptibly(
    timeout_seconds: float,
    cancellation: CancellationToken,
    emergency_stop: EmergencyStopLatch,
    *,
    monotonic=time.monotonic,
) -> WaitResult:
    """Wait without an uninterruptible sleep path.

    Cancellation and emergency-stop state share the same condition in the
    scheduler, preventing a lost wake between state inspection and wait.
    """

    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise ValueError("timeout_seconds must be finite and non-negative")
    if cancellation.signal is not emergency_stop.signal:
        raise ValueError(
            "cancellation and emergency stop must share one wake signal"
        )

    signal = cancellation.signal
    deadline = monotonic() + timeout_seconds
    with signal.condition:
        while True:
            if emergency_stop._triggered:
                return WaitResult.EMERGENCY_STOPPED
            if cancellation._cancelled:
                return WaitResult.CANCELLED

            remaining = deadline - monotonic()
            if remaining <= 0:
                return WaitResult.COMPLETED
            signal.condition.wait(timeout=remaining)
