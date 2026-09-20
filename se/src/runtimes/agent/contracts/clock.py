from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class ExecutionClock(Protocol):
    """Clock domains used by Agent execution timing."""

    def monotonic(self) -> float:
        """Return process-local monotonic seconds."""
        ...

    def now_utc(self) -> datetime:
        """Return the current timezone-aware UTC wall clock."""
        ...


@dataclass(frozen=True, slots=True)
class SystemExecutionClock:
    """Production clock backed by Python's monotonic and UTC clocks."""

    def monotonic(self) -> float:
        return time.monotonic()

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


__all__ = ["ExecutionClock", "SystemExecutionClock"]
