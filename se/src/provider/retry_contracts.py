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


@dataclass(slots=True)
class ProviderCallBudget:
    """Process-local deadline and retry budget for one logical provider call.

    The same object is intended to be shared across every fallback candidate
    for one logical inference/provider call. It is deliberately non-durable:
    AE-R10 does not add provider retry state to Task/Agent persistence.
    """

    deadline_monotonic: float
    max_retries: int
    retries_used: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.deadline_monotonic, bool):
            raise TypeError("deadline_monotonic must be numeric")
        self.deadline_monotonic = float(self.deadline_monotonic)
        if not math.isfinite(self.deadline_monotonic):
            raise ValueError("deadline_monotonic must be finite")
        if not isinstance(self.max_retries, int) or isinstance(
            self.max_retries, bool
        ):
            raise TypeError("max_retries must be an integer")
        if not isinstance(self.retries_used, int) or isinstance(
            self.retries_used, bool
        ):
            raise TypeError("retries_used must be an integer")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.retries_used < 0:
            raise ValueError("retries_used must be non-negative")
        if self.retries_used > self.max_retries:
            raise ValueError("retries_used cannot exceed max_retries")

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
    def retries_remaining(self) -> int:
        return self.max_retries - self.retries_used

    def remaining_seconds(self, *, now_monotonic: float) -> float:
        if isinstance(now_monotonic, bool):
            raise TypeError("now_monotonic must be numeric")
        now = float(now_monotonic)
        if not math.isfinite(now):
            raise ValueError("now_monotonic must be finite")
        return max(0.0, self.deadline_monotonic - now)

    def try_consume_retry(self) -> bool:
        """Consume one additional network retry token if one remains."""

        if self.retries_used >= self.max_retries:
            return False
        self.retries_used += 1
        return True
