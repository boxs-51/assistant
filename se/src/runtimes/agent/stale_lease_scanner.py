from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final

from .contracts import ExecutionClock, SystemExecutionClock
from .persistence import DurableAgentStore


MAX_STALE_LEASE_SCAN_PAGE_SIZE: Final[int] = 100


def _positive_int(value: int, *, field: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be <= {maximum}")
    return value


def _positive_finite(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a positive finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field} must be a positive finite number")
    return normalized


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must have UTC offset zero")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class StaleLeaseScanPolicy:
    page_size: int = 100
    max_pages: int = 10
    max_rows: int = 1000
    max_duration_seconds: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "page_size",
            _positive_int(
                self.page_size,
                field="page_size",
                maximum=MAX_STALE_LEASE_SCAN_PAGE_SIZE,
            ),
        )
        object.__setattr__(
            self,
            "max_pages",
            _positive_int(self.max_pages, field="max_pages"),
        )
        object.__setattr__(
            self,
            "max_rows",
            _positive_int(self.max_rows, field="max_rows"),
        )
        object.__setattr__(
            self,
            "max_duration_seconds",
            _positive_finite(
                self.max_duration_seconds,
                field="max_duration_seconds",
            ),
        )


@dataclass(frozen=True, slots=True)
class StaleLeaseObservation:
    execution_id: str
    owner_instance_id: str
    lease_generation: int
    lease_expires_at: datetime
    state: str
    revision: int

    @classmethod
    def from_record(cls, record) -> "StaleLeaseObservation":
        execution_id = str(record.id)
        owner_instance_id = record.owner_instance_id
        lease_expires_at = record.lease_expires_at
        if not execution_id:
            raise ValueError("expired lease observation must have an execution id")
        if not isinstance(owner_instance_id, str) or not owner_instance_id:
            raise ValueError("expired lease observation must have an owner")
        if (
            isinstance(record.lease_generation, bool)
            or not isinstance(record.lease_generation, int)
            or record.lease_generation <= 0
        ):
            raise ValueError(
                "expired lease observation must have positive lease generation"
            )
        return cls(
            execution_id=execution_id,
            owner_instance_id=owner_instance_id,
            lease_generation=record.lease_generation,
            lease_expires_at=_aware_utc(
                lease_expires_at,
                field="lease_expires_at",
            ),
            state=str(record.state),
            revision=int(record.revision),
        )


class StaleLeaseSweepStopReason(str, Enum):
    EXHAUSTED = "EXHAUSTED"
    MAX_PAGES = "MAX_PAGES"
    MAX_ROWS = "MAX_ROWS"
    MAX_DURATION = "MAX_DURATION"


@dataclass(frozen=True, slots=True)
class StaleLeaseSweepResult:
    scan_cutoff_utc: datetime
    started_monotonic: float
    finished_monotonic: float
    pages_fetched: int
    observations: tuple[StaleLeaseObservation, ...]
    stop_reason: StaleLeaseSweepStopReason
    observation_errors: tuple[str, ...] = ()

    @property
    def rows_observed(self) -> int:
        return len(self.observations)

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self.finished_monotonic - self.started_monotonic)


class StaleLeaseScanCoordinator:
    """Bounded, serialized, observation-only traversal of D1 stale leases."""

    def __init__(
        self,
        store: DurableAgentStore,
        *,
        policy: StaleLeaseScanPolicy | None = None,
        clock: ExecutionClock | None = None,
    ) -> None:
        self._store = store
        self._policy = policy or StaleLeaseScanPolicy()
        self._clock = clock or SystemExecutionClock()
        self._scan_lock = asyncio.Lock()
        self._after_expiry: datetime | None = None
        self._after_execution_id: str | None = None

    @property
    def cursor(self) -> tuple[datetime | None, str | None]:
        """Return process-local traversal state; never mutation authority."""

        return self._after_expiry, self._after_execution_id

    async def scan_once(self) -> StaleLeaseSweepResult:
        # Lock acquisition is outside the sweep. A queued caller starts its own
        # bounded sweep only after the prior sweep has completed.
        async with self._scan_lock:
            return await self._scan_locked()

    async def _scan_locked(self) -> StaleLeaseSweepResult:
        scan_cutoff_utc = _aware_utc(
            self._clock.now_utc(),
            field="scan_cutoff_utc",
        )
        started_monotonic = self._clock.monotonic()

        observations: list[StaleLeaseObservation] = []
        observation_errors: list[str] = []
        pages_fetched = 0
        rows_returned = 0
        after_expiry = self._after_expiry
        after_execution_id = self._after_execution_id
        last_returned_expiry: datetime | None = None
        last_returned_execution_id: str | None = None
        stop_reason = StaleLeaseSweepStopReason.EXHAUSTED

        while True:
            elapsed = self._clock.monotonic() - started_monotonic
            if elapsed >= self._policy.max_duration_seconds:
                stop_reason = StaleLeaseSweepStopReason.MAX_DURATION
                break
            if pages_fetched >= self._policy.max_pages:
                stop_reason = StaleLeaseSweepStopReason.MAX_PAGES
                break

            remaining_rows = self._policy.max_rows - rows_returned
            if remaining_rows <= 0:
                stop_reason = StaleLeaseSweepStopReason.MAX_ROWS
                break

            request_limit = min(self._policy.page_size, remaining_rows)
            remaining_duration = self._policy.max_duration_seconds - elapsed
            try:
                rows = await asyncio.wait_for(
                    self._store.list_expired_execution_leases(
                        cutoff_utc=scan_cutoff_utc,
                        limit=request_limit,
                        after_expiry=after_expiry,
                        after_execution_id=after_execution_id,
                    ),
                    timeout=remaining_duration,
                )
            except asyncio.TimeoutError:
                stop_reason = StaleLeaseSweepStopReason.MAX_DURATION
                break
            pages_fetched += 1

            elapsed = self._clock.monotonic() - started_monotonic
            if elapsed >= self._policy.max_duration_seconds:
                stop_reason = StaleLeaseSweepStopReason.MAX_DURATION
                break

            raw_page = tuple(rows)
            rows_returned += len(raw_page)
            for record in raw_page:
                execution_id = str(getattr(record, "id", ""))
                raw_expiry = _aware_utc(
                    getattr(record, "lease_expires_at", None),
                    field="lease_expires_at",
                )
                if not execution_id:
                    raise ValueError(
                        "expired lease scan row must have an execution id"
                    )

                # Cursor progression is based on the raw ordered row returned by
                # D1, not on successful semantic observation construction.
                # This lets one malformed row be isolated without pinning the
                # bounded traversal prefix forever.
                last_returned_expiry = raw_expiry
                last_returned_execution_id = execution_id
                after_expiry = raw_expiry
                after_execution_id = execution_id

                try:
                    observations.append(
                        StaleLeaseObservation.from_record(record)
                    )
                except Exception as exc:
                    observation_errors.append(
                        f"{execution_id}:{type(exc).__name__}:{exc}"
                    )

            if len(raw_page) < request_limit:
                stop_reason = StaleLeaseSweepStopReason.EXHAUSTED
                break
            if rows_returned >= self._policy.max_rows:
                stop_reason = StaleLeaseSweepStopReason.MAX_ROWS
                break
            if pages_fetched >= self._policy.max_pages:
                stop_reason = StaleLeaseSweepStopReason.MAX_PAGES
                break

        # Cursor is process-local traversal state only.  A bounded stop
        # continues strictly after the last observation actually returned to
        # the caller.  Exhaustion completes one ordered pass and wraps.
        if stop_reason == StaleLeaseSweepStopReason.EXHAUSTED:
            self._after_expiry = None
            self._after_execution_id = None
        elif (
            last_returned_expiry is not None
            and last_returned_execution_id is not None
        ):
            self._after_expiry = last_returned_expiry
            self._after_execution_id = last_returned_execution_id

        finished_monotonic = self._clock.monotonic()
        return StaleLeaseSweepResult(
            scan_cutoff_utc=scan_cutoff_utc,
            started_monotonic=started_monotonic,
            finished_monotonic=finished_monotonic,
            pages_fetched=pages_fetched,
            observations=tuple(observations),
            stop_reason=stop_reason,
            observation_errors=tuple(observation_errors),
        )


__all__ = [
    "MAX_STALE_LEASE_SCAN_PAGE_SIZE",
    "StaleLeaseObservation",
    "StaleLeaseScanCoordinator",
    "StaleLeaseScanPolicy",
    "StaleLeaseSweepResult",
    "StaleLeaseSweepStopReason",
]
