import asyncio
import math
import time
from typing import Any, AsyncGenerator, Awaitable, Callable

import httpx
import structlog

from ..circuit_breaker import CircuitBreakerManager, CircuitBreakerOpenError
from ..domain.schemas import GatewayResponse, GatewayStreamChunk
from ..infrastructure.config.schemas import ProviderSettings
from .core.provider import BaseProvider
from .exceptions import (
    ProviderDeadlineExceededError,
    ProviderError,
    wrap_provider_exception,
)
from .policies.retry import RetryPolicy
from .retry_contracts import ProviderCallBudget

logger = structlog.get_logger(__name__)


class ProviderExecutor:
    """Execute one provider request under resilience policies."""

    def __init__(
        self,
        circuit_breaker_manager: CircuitBreakerManager,
        retry_policy: RetryPolicy | None = None,
        *,
        max_retries: int | None = None,
        config: ProviderSettings = None,
    ):
        self.breaker_manager = circuit_breaker_manager
        self.retry_policy = retry_policy or RetryPolicy(
            max_retries=max_retries,
            config=config,
        )

    async def is_provider_healthy(self, provider_name: str) -> bool:
        breaker = await self.breaker_manager.get_breaker(provider_name)
        return not await breaker.is_open()

    def _get_error_metric_label(self, error: httpx.RequestError) -> str:
        if isinstance(error, httpx.ConnectError):
            return "connect_error"
        if isinstance(error, httpx.ReadTimeout):
            return "read_timeout"
        if isinstance(error, httpx.WriteTimeout):
            return "write_timeout"
        if isinstance(error, httpx.PoolTimeout):
            return "pool_timeout"
        return "request_error"

    @staticmethod
    def _remaining_or_raise(
        call_budget: ProviderCallBudget,
        provider_name: str,
    ) -> float:
        remaining = call_budget.remaining_seconds(
            now_monotonic=time.monotonic()
        )
        if remaining <= 0:
            raise ProviderDeadlineExceededError(
                "Provider call deadline exceeded before attempt.",
                provider_name=provider_name,
            )
        return remaining

    @staticmethod
    def _bounded_attempt_timeout(
        configured_timeout: Any,
        remaining: float,
    ) -> float:
        """Return a positive attempt timeout bounded by logical remaining time."""

        if isinstance(configured_timeout, bool) or configured_timeout is None:
            return remaining
        if isinstance(configured_timeout, (int, float)):
            configured = float(configured_timeout)
            if math.isfinite(configured) and configured > 0:
                return min(configured, remaining)
        # Budget-aware execution must remain bounded even when a caller
        # supplied a non-scalar timeout object or invalid value.
        return remaining

    async def execute(
        self,
        *,
        call_budget: ProviderCallBudget | None = None,
        **kwargs,
    ) -> GatewayResponse:
        """Execute chat with circuit-breaker and optional logical-call budget."""

        provider = kwargs.get("provider")
        breaker = await self.breaker_manager.get_breaker(provider.name)
        provider_attempted = False

        async def execution_func():
            nonlocal provider_attempted
            attempt_kwargs = dict(kwargs)
            if call_budget is not None:
                remaining = self._remaining_or_raise(
                    call_budget,
                    provider.name,
                )
                attempt_kwargs["timeout"] = self._bounded_attempt_timeout(
                    attempt_kwargs.get("timeout"),
                    remaining,
                )
            provider_attempted = True
            return await provider.chat.chat(**attempt_kwargs)

        try:
            if call_budget is not None:
                self._remaining_or_raise(call_budget, provider.name)

            await breaker.before_request()

            if call_budget is None:
                response = await self.retry_policy.apply(
                    execution_func,
                    provider.name,
                )
            else:
                response = await self.retry_policy.apply(
                    execution_func,
                    provider.name,
                    call_budget=call_budget,
                )

            await breaker.on_success()
            return response

        except ProviderDeadlineExceededError:
            # An expired caller budget is not a provider failure when no
            # request reached the provider. If an earlier attempt did run,
            # retain the existing one-failure-per-executor-call accounting.
            if provider_attempted:
                await breaker.on_failure()
            raise

        except CircuitBreakerOpenError as e:
            logger.warning(
                "Skipping provider call, circuit breaker is open.",
                provider=provider.name,
            )
            raise ProviderError(
                f"Circuit breaker is open for {provider.name}",
                provider_name=provider.name,
            ) from e

        except Exception as e:
            await breaker.on_failure()

            if isinstance(e, httpx.HTTPStatusError):
                error_label = str(e.response.status_code)
            elif isinstance(e, httpx.RequestError):
                error_label = self._get_error_metric_label(e)
            else:
                error_label = "unexpected_error"

            logger.warning(
                "Provider execution failed after all retries.",
                provider=provider.name,
                error=str(e),
                error_type=type(e).__name__,
                error_label=error_label,
            )
            normalized = wrap_provider_exception(e, provider.name)
            if normalized is e:
                raise
            raise normalized from e

    async def execute_stream(
        self,
        *,
        call_budget: ProviderCallBudget | None = None,
        timeout: float | None = None,
        **kwargs,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Execute one stream under the shared logical-call deadline.

        Streaming never performs a provider-local retry. The handler may move
        to another provider only before the first visible chunk.
        """

        provider = kwargs.get("provider")
        breaker = await self.breaker_manager.get_breaker(provider.name)
        provider_attempted = False

        try:
            if call_budget is not None:
                self._remaining_or_raise(call_budget, provider.name)

            await breaker.before_request()

            attempt_kwargs = dict(kwargs)
            stream_remaining: float | None = None
            if call_budget is not None:
                stream_remaining = self._remaining_or_raise(
                    call_budget,
                    provider.name,
                )
                attempt_kwargs["timeout"] = self._bounded_attempt_timeout(
                    timeout,
                    stream_remaining,
                )
            elif timeout is not None:
                attempt_kwargs["timeout"] = timeout

            provider_attempted = True
            logger.info(
                "Starting streaming from provider",
                provider=provider.name,
            )

            if stream_remaining is None:
                async for chunk in provider.chat.chat_stream(
                    **attempt_kwargs
                ):
                    yield chunk
            else:
                stream_timeout = asyncio.timeout(stream_remaining)
                try:
                    async with stream_timeout:
                        async for chunk in provider.chat.chat_stream(
                            **attempt_kwargs
                        ):
                            yield chunk
                except TimeoutError as exc:
                    if stream_timeout.expired():
                        raise ProviderDeadlineExceededError(
                            "Provider stream deadline exceeded.",
                            provider_name=provider.name,
                        ) from exc
                    raise

            await breaker.on_success()

        except ProviderDeadlineExceededError:
            if provider_attempted:
                await breaker.on_failure()
            raise

        except CircuitBreakerOpenError as e:
            logger.warning(
                "Skipping provider stream, circuit breaker is open.",
                provider=provider.name,
            )
            raise ProviderError(
                f"Circuit breaker is open for {provider.name}",
                provider_name=provider.name,
            ) from e

        except Exception as e:
            await breaker.on_failure()
            if isinstance(e, httpx.HTTPStatusError):
                error_label = str(e.response.status_code)
            elif isinstance(e, httpx.RequestError):
                error_label = self._get_error_metric_label(e)
            else:
                error_label = "unexpected_error"

            logger.warning(
                "Provider stream execution failed.",
                provider=provider.name,
                error=str(e),
                error_type=type(e).__name__,
                error_label=error_label,
            )
            normalized = wrap_provider_exception(e, provider.name)
            if normalized is e:
                raise
            raise normalized from e

    async def execute_generic(
        self,
        provider: BaseProvider,
        execution_callable: Callable[..., Awaitable[Any]],
        *,
        call_budget: ProviderCallBudget | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Execute a generic provider operation with the same retry budget."""

        breaker = await self.breaker_manager.get_breaker(provider.name)
        provider_attempted = False

        async def execution_func():
            nonlocal provider_attempted
            if call_budget is not None:
                remaining = self._remaining_or_raise(
                    call_budget,
                    provider.name,
                )
                attempt_timeout = self._bounded_attempt_timeout(
                    timeout,
                    remaining,
                )
                provider_attempted = True
                return await execution_callable(attempt_timeout)

            provider_attempted = True
            return await execution_callable()

        try:
            if call_budget is not None:
                self._remaining_or_raise(call_budget, provider.name)

            await breaker.before_request()

            if call_budget is None:
                response = await self.retry_policy.apply(
                    execution_func,
                    provider.name,
                )
            else:
                response = await self.retry_policy.apply(
                    execution_func,
                    provider.name,
                    call_budget=call_budget,
                )

            await breaker.on_success()
            return response

        except ProviderDeadlineExceededError:
            if provider_attempted:
                await breaker.on_failure()
            raise

        except CircuitBreakerOpenError as e:
            logger.warning(
                "Skipping provider call, circuit breaker is open.",
                provider=provider.name,
            )
            raise ProviderError(
                f"Circuit breaker is open for {provider.name}",
                provider_name=provider.name,
            ) from e

        except Exception as e:
            await breaker.on_failure()

            if isinstance(e, httpx.HTTPStatusError):
                error_label = str(e.response.status_code)
            elif isinstance(e, httpx.RequestError):
                error_label = self._get_error_metric_label(e)
            else:
                error_label = "unexpected_error"

            logger.warning(
                "Provider generic execution failed after all retries.",
                provider=provider.name,
                error=str(e),
                error_type=type(e).__name__,
                error_label=error_label,
            )
            normalized = wrap_provider_exception(e, provider.name)
            if normalized is e:
                raise
            raise normalized from e
