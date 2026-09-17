from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict


class TemporalContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_time: datetime
    current_date: str
    timezone: str
    utc_offset: str

    def as_system_text(self) -> str:
        return (
            "[TEMPORAL CONTEXT]\n"
            f"current_time: {self.current_time.isoformat()}\n"
            f"current_date: {self.current_date}\n"
            f"timezone: {self.timezone}\n"
            f"utc_offset: {self.utc_offset}"
        )


class TemporalContextProvider:
    """Server-owned current-time context; call once immediately before inference."""

    def __init__(self, clock: Callable[[ZoneInfo], datetime] | None = None) -> None:
        self._clock = clock or (lambda zone: datetime.now(zone))

    def current(self, timezone_name: str | None = None) -> TemporalContext:
        name = timezone_name or "UTC"
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError:
            name = "UTC"
            zone = ZoneInfo(name)
        now = self._clock(zone)
        offset = now.strftime("%z")
        formatted_offset = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
        return TemporalContext(
            current_time=now,
            current_date=now.date().isoformat(),
            timezone=name,
            utc_offset=formatted_offset,
        )


__all__ = ["TemporalContext", "TemporalContextProvider"]
