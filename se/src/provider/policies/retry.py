import asyncio
import random

import httpx
import structlog

from ..exceptions import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    ResponseValidationError,
    wrap_provider_exception,
)
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

    async def apply(self, execution_func, provider_name: str):
        """Execute with existing local backoff after pre-scheduling normalization."""

        for attempt in range(self.max_retries + 1):
            try:
                return await execution_func()
            except Exception as raw_error:
                error = wrap_provider_exception(raw_error, provider_name)
                status_code = getattr(error, "status_code", None)
                error_code = getattr(error, "error_code", None)

                # R10-B normalizes raw evidence before classification, but it
                # must not broaden the legacy retry set. Raw HTTP and transport
                # failures keep their pre-R10 retryability until R10-C owns the
                # scheduling policy.
                if isinstance(raw_error, httpx.HTTPStatusError):
                    raw_status = raw_error.response.status_code
                    retryable = (
                        raw_status == 429
                        or 500 <= raw_status < 600
                    )
                elif isinstance(raw_error, httpx.RequestError):
                    retryable = isinstance(
                        raw_error,
                        (httpx.TimeoutException, httpx.ConnectError),
                    )
                else:
                    retryable = self._is_retryable(error)

                if not retryable or attempt >= self.max_retries:
                    if error is raw_error:
                        raise
                    raise error from raw_error

                # AE-R10-B intentionally preserves the existing local
                # exponential+jitter scheduling. Provider retry hints are
                # normalized here for R10-C, but are not consumed yet.
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
