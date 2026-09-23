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


async def await_with_provider_deadline(
    operation: Callable[[float], Awaitable[Any]],
    *,
    call_budget: ProviderCallBudget,
    provider_name: str,
    timeout_message: str,
    now_monotonic: Callable[[], float] | None = None,
) -> Any:
    """Run one owned provider await under the logical monotonic deadline.

    The child is cancelled and retrieved on logical timeout or caller
    cancellation. A result that becomes visible only after the logical
    deadline is rejected as deadline-exceeded rather than accepted as success.
    """

    clock = time.monotonic if now_monotonic is None else now_monotonic
    remaining = call_budget.remaining_seconds(
        now_monotonic=clock()
    )
    if remaining <= 0:
        raise ProviderDeadlineExceededError(
            timeout_message,
            provider_name=provider_name,
        )

    child = asyncio.create_task(operation(remaining))
    try:
        done, _ = await asyncio.wait(
            {child},
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if child not in done:
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)
            raise ProviderDeadlineExceededError(
                timeout_message,
                provider_name=provider_name,
            )

        result = await child
        if call_budget.remaining_seconds(
            now_monotonic=clock()
        ) <= 0:
            raise ProviderDeadlineExceededError(
                timeout_message,
                provider_name=provider_name,
            )
        return result
    except asyncio.CancelledError:
        if not child.done():
            child.cancel()
        await asyncio.gather(child, return_exceptions=True)
        raise
    finally:
        if not child.done():
            child.cancel()
            await asyncio.gather(child, return_exceptions=True)


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
            if call_budget is None:
                provider_attempted = True
                return await provider.chat.chat(**attempt_kwargs)

            async def run_attempt(remaining: float):
                nonlocal provider_attempted
                bounded_kwargs = dict(attempt_kwargs)
                bounded_kwargs["timeout"] = self._bounded_attempt_timeout(
                    bounded_kwargs.get("timeout"),
                    remaining,
                )
                provider_attempted = True
                return await provider.chat.chat(**bounded_kwargs)

            return await await_with_provider_deadline(
                run_attempt,
                call_budget=call_budget,
                provider_name=provider.name,
                timeout_message="Provider call deadline exceeded during attempt.",
            )

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

            logger.info(
                "Starting streaming from provider",
                provider=provider.name,
            )

            stream_iterator = provider.chat.chat_stream(
                **attempt_kwargs
            ).__aiter__()
            next_chunk_task: asyncio.Task | None = None
            try:
                while True:
                    try:
                        if call_budget is None:
                            provider_attempted = True
                            chunk = await stream_iterator.__anext__()
                        else:
                            remaining = self._remaining_or_raise(
                                call_budget,
                                provider.name,
                            )
                            provider_attempted = True
                            next_chunk_task = asyncio.create_task(
                                stream_iterator.__anext__()
                            )
                            done, _ = await asyncio.wait(
                                {next_chunk_task},
                                timeout=remaining,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if next_chunk_task not in done:
                                next_chunk_task.cancel()
                                await asyncio.gather(
                                    next_chunk_task,
                                    return_exceptions=True,
                                )
                                next_chunk_task = None
                                raise ProviderDeadlineExceededError(
                                    "Provider stream deadline exceeded.",
                                    provider_name=provider.name,
                                )
                            chunk = await next_chunk_task
                            next_chunk_task = None
                    except StopAsyncIteration:
                        next_chunk_task = None
                        break

                    yield chunk
            finally:
                if next_chunk_task is not None:
                    if not next_chunk_task.done():
                        next_chunk_task.cancel()
                    await asyncio.gather(
                        next_chunk_task,
                        return_exceptions=True,
                    )
                aclose = getattr(stream_iterator, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception as cleanup_error:
                        logger.warning(
                            "Provider stream cleanup failed.",
                            provider=provider.name,
                            error=str(cleanup_error),
                            error_type=type(cleanup_error).__name__,
                        )

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
            if call_budget is None:
                provider_attempted = True
                return await execution_callable()

            async def run_attempt(remaining: float):
                nonlocal provider_attempted
                attempt_timeout = self._bounded_attempt_timeout(
                    timeout,
                    remaining,
                )
                provider_attempted = True
                return await execution_callable(attempt_timeout)

            return await await_with_provider_deadline(
                run_attempt,
                call_budget=call_budget,
                provider_name=provider.name,
                timeout_message=(
                    "Provider generic call deadline exceeded during attempt."
                ),
            )

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
