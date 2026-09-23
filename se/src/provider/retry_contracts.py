from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class ProviderRetryHintSource(StrEnum):
    """Canonical source of a provider-requested retry delay."""

    RETRY_AFTER = "RETRY_AFTER"
    GOOGLE_RPC_RETRY_INFO = "GOOGLE_RPC_RETRY_INFO"


@dataclass(frozen=True, slots=True)
class ProviderRetryHint:
    """Normalized provider retry-delay hint.

    Parsing provider-specific headers/payloads belongs to AE-R10-B. R10-A
    freezes only the normalized representation consumed by later stages.
    """

    retry_after_seconds: float
    source: ProviderRetryHintSource

    def __post_init__(self) -> None:
        if isinstance(self.retry_after_seconds, bool):
            raise TypeError("retry_after_seconds must be numeric")
        value = float(self.retry_after_seconds)
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                "retry_after_seconds must be a finite non-negative value"
            )
        source = (
            self.source
            if isinstance(self.source, ProviderRetryHintSource)
            else ProviderRetryHintSource(self.source)
        )
        object.__setattr__(self, "retry_after_seconds", value)
        object.__setattr__(self, "source", source)


class ProviderCallBudget:
    """Process-local deadline and retry budget for one logical provider call.

    One instance is shared across every fallback candidate for the logical
    call. Public budget state is read-only; additional retry consumption can
    happen only through try_consume_retry().
    """

    __slots__ = (
        "_deadline_monotonic",
        "_max_retries",
        "_retries_used",
    )

    def __init__(
        self,
        *,
        deadline_monotonic: float,
        max_retries: int,
    ) -> None:
        if isinstance(deadline_monotonic, bool):
            raise TypeError("deadline_monotonic must be numeric")
        deadline = float(deadline_monotonic)
        if not math.isfinite(deadline):
            raise ValueError("deadline_monotonic must be finite")
        if not isinstance(max_retries, int) or isinstance(max_retries, bool):
            raise TypeError("max_retries must be an integer")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")

        self._deadline_monotonic = deadline
        self._max_retries = max_retries
        self._retries_used = 0

    @classmethod
    def from_timeout(
        cls,
        *,
        now_monotonic: float,
        timeout_seconds: float,
        max_retries: int,
    ) -> "ProviderCallBudget":
        if isinstance(now_monotonic, bool) or isinstance(
            timeout_seconds, bool
        ):
            raise TypeError("timeout inputs must be numeric")
        now = float(now_monotonic)
        timeout = float(timeout_seconds)
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        return cls(
            deadline_monotonic=now + timeout,
            max_retries=max_retries,
        )

    @property
    def deadline_monotonic(self) -> float:
        return self._deadline_monotonic

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retries_used(self) -> int:
        return self._retries_used

    @property
    def retries_remaining(self) -> int:
        return self._max_retries - self._retries_used

    def remaining_seconds(self, *, now_monotonic: float) -> float:
        if isinstance(now_monotonic, bool):
            raise TypeError("now_monotonic must be numeric")
        now = float(now_monotonic)
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")
        return max(0.0, self._deadline_monotonic - now)

    def try_consume_retry(self) -> bool:
        """Consume one additional network retry token if one remains."""

        if self._retries_used >= self._max_retries:
            return False
        self._retries_used += 1
        return True
