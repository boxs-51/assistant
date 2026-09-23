import asyncio
import random
import time

import httpx
import structlog

from ..exceptions import (
    ProviderAuthenticationError,
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    ResponseValidationError,
    wrap_provider_exception,
)
from ..retry_contracts import ProviderCallBudget
from ...circuit_breaker import CircuitBreakerOpenError
from ...infrastructure.config.schemas import ProviderSettings

logger = structlog.get_logger(__name__)


class RetryPolicy:
    """Provider-local retry classification and backoff policy."""

    def __init__(
        self,
        max_retries: int | None = None,
        config: ProviderSettings = None,
    ):
        if max_retries is not None:
            self.max_retries = max_retries
        elif config is not None:
            self.max_retries = config.provider.retry

    def _is_retryable(self, error: Exception) -> bool:
        if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
            return True
        if isinstance(error, ProviderRateLimitError):
            return True
        if isinstance(error, ProviderUnavailableError):
            return True
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
            return status_code == 429 or 500 <= status_code < 600
        if isinstance(error, CircuitBreakerOpenError):
            return False
        if isinstance(error, ProviderAuthenticationError):
            return False
        if isinstance(error, ResponseValidationError):
            return False
        if isinstance(error, ProviderError):
            return False
        return False

    @staticmethod
    def _normalize_error(
        raw_error: Exception,
        provider_name: str,
    ) -> Exception:
        if isinstance(
            raw_error,
            (ProviderError, httpx.HTTPStatusError, httpx.RequestError),
        ):
            return wrap_provider_exception(raw_error, provider_name)
        return raw_error

    def _legacy_retryable(
        self,
        raw_error: Exception,
        normalized_error: Exception,
    ) -> bool:
        """Preserve the pre-R10 raw retry set.

        R10-C changes scheduling/accounting only. Raw HTTP 408 and generic
        RequestError remain non-retryable unless a later stage explicitly
        re-freezes that taxonomy.
        """

        if isinstance(raw_error, httpx.HTTPStatusError):
            raw_status = raw_error.response.status_code
            return raw_status == 429 or 500 <= raw_status < 600
        if isinstance(raw_error, httpx.RequestError):
            return isinstance(
                raw_error,
                (httpx.TimeoutException, httpx.ConnectError),
            )
        return self._is_retryable(normalized_error)

    @staticmethod
    def _raise_current(
        raw_error: Exception,
        normalized_error: Exception,
    ) -> None:
        if normalized_error is raw_error:
            raise raw_error
        raise normalized_error from raw_error

    async def _apply_legacy(self, execution_func, provider_name: str):
        """R10-B-compatible behavior when no logical call budget is supplied."""

        for attempt in range(self.max_retries + 1):
            try:
                return await execution_func()
            except Exception as raw_error:
                error = self._normalize_error(raw_error, provider_name)
                status_code = getattr(error, "status_code", None)
                error_code = getattr(error, "error_code", None)
                retryable = self._legacy_retryable(raw_error, error)

                if not retryable or attempt >= self.max_retries:
                    self._raise_current(raw_error, error)

                delay = (2 ** attempt) + random.uniform(0, 1)

                logger.warning(
                    "Retrying provider execution due to transient error.",
                    provider=provider_name,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    delay=round(delay, 2),
                    error_type=error.__class__.__name__,
                    status_code=status_code,
                    error_code=error_code,
                    retry_after_seconds=getattr(
                        error,
                        "retry_after_seconds",
                        None,
                    ),
                )

                await asyncio.sleep(delay)

    async def _apply_budgeted(
        self,
        execution_func,
        provider_name: str,
        call_budget: ProviderCallBudget,
    ):
        """Execute using one externally-owned logical provider call budget."""

        provider_retry_index = 0

        while True:
            remaining = call_budget.remaining_seconds(
                now_monotonic=time.monotonic()
            )
            if remaining <= 0:
                raise ProviderDeadlineExceededError(
                    "Provider call deadline exceeded before attempt.",
                    provider_name=provider_name,
                )

            try:
                return await execution_func()
            except Exception as raw_error:
                error = self._normalize_error(raw_error, provider_name)
                retryable = self._legacy_retryable(raw_error, error)

                if not retryable:
                    self._raise_current(raw_error, error)

                if call_budget.retries_remaining <= 0:
                    self._raise_current(raw_error, error)

                local_delay = (
                    2 ** provider_retry_index
                ) + random.uniform(0, 1)
                retry_after = getattr(
                    error,
                    "retry_after_seconds",
                    None,
                )
                effective_delay = max(
                    local_delay,
                    0.0 if retry_after is None else retry_after,
                )

                remaining = call_budget.remaining_seconds(
                    now_monotonic=time.monotonic()
                )
                if remaining <= 0 or effective_delay >= remaining:
                    self._raise_current(raw_error, error)

                logger.warning(
                    "Retrying provider execution within logical call budget.",
                    provider=provider_name,
                    retry_index=provider_retry_index + 1,
                    retries_used=call_budget.retries_used,
                    retries_remaining=call_budget.retries_remaining,
                    delay=round(effective_delay, 2),
                    remaining=round(remaining, 2),
                    error_type=error.__class__.__name__,
                    status_code=getattr(error, "status_code", None),
                    error_code=getattr(error, "error_code", None),
                    retry_after_seconds=retry_after,
                )

                # Cancellation here propagates as CancelledError and therefore
                # consumes no retry token and starts no additional attempt.
                await asyncio.sleep(effective_delay)

                remaining = call_budget.remaining_seconds(
                    now_monotonic=time.monotonic()
                )
                if remaining <= 0:
                    raise ProviderDeadlineExceededError(
                        "Provider call deadline exceeded before retry attempt.",
                        provider_name=provider_name,
                    ) from error

                if not call_budget.try_consume_retry():
                    self._raise_current(raw_error, error)

                provider_retry_index += 1

    async def apply(
        self,
        execution_func,
        provider_name: str,
        *,
        call_budget: ProviderCallBudget | None = None,
    ):
        """Execute with legacy or injected logical-call retry accounting."""

        if call_budget is None:
            return await self._apply_legacy(
                execution_func,
                provider_name,
            )
        return await self._apply_budgeted(
            execution_func,
            provider_name,
            call_budget,
        )
