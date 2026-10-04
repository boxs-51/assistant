import asyncio
import math
import time
from typing import Any, AsyncGenerator, Awaitable, Callable

import httpx
import structlog

from ..circuit_breaker import (
    CircuitBreakerManager,
    CircuitBreakerOpenError,
    CircuitBreakerState,
)
from ..domain.schemas import GatewayResponse, GatewayStreamChunk
from ..infrastructure.config.schemas import ProviderSettings
from .core.provider import BaseProvider
from .exceptions import (
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderFirstResponseTimeoutError,
    ProviderStreamIdleTimeoutError,
    ProviderRecoveryAuthorityLostError,
    ProviderRecoveryGuardError,
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

        result: Any = None
        terminal_error: Exception | None = None
        terminal_cancel: asyncio.CancelledError | None = None
        try:
            result = child.result()
        except asyncio.CancelledError as error:
            terminal_cancel = error
        except Exception as error:
            terminal_error = error

        if call_budget.remaining_seconds(
            now_monotonic=clock()
        ) <= 0:
            raise ProviderDeadlineExceededError(
                timeout_message,
                provider_name=provider_name,
            )

        if terminal_cancel is not None:
            raise terminal_cancel
        if terminal_error is not None:
            raise terminal_error
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

    @staticmethod
    def _release_half_open_probe_without_provider_outcome(
        breaker: Any,
        *,
        probe_acquired: bool,
    ) -> None:
        """Release this call's HALF_OPEN trial without recording an outcome.

        Recovery/deadline guards can revoke authority after before_request()
        acquired the HALF_OPEN probe but before any physical provider send.
        In that case neither provider success nor provider failure occurred, so
        the breaker state/counters must remain unchanged while this call's
        exclusive trial lock is relinquished for a later authorized request.
        """

        if not probe_acquired:
            return
        if getattr(breaker, "current_state", None) != CircuitBreakerState.HALF_OPEN:
            return
        probe_lock = getattr(breaker, "_half_open_lock", None)
        if probe_lock is not None and probe_lock.locked():
            probe_lock.release()

    async def execute(
        self,
        *,
        call_budget: ProviderCallBudget | None = None,
        recovery_pre_attempt_guard: Callable[[], Awaitable[None]] | None = None,
        **kwargs,
    ) -> GatewayResponse:
        """Execute chat with circuit-breaker and optional logical-call budget."""

        provider = kwargs.get("provider")
        breaker = await self.breaker_manager.get_breaker(provider.name)
        provider_attempted = False
        half_open_probe_acquired = False
        recovery_guard_aborted_before_send = False
        recovery_guard_control_flow_error: BaseException | None = None

        async def run_recovery_pre_attempt_guard() -> None:
            nonlocal recovery_guard_aborted_before_send
            nonlocal recovery_guard_control_flow_error
            if recovery_pre_attempt_guard is None:
                return
            try:
                await recovery_pre_attempt_guard()
            except ProviderRecoveryAuthorityLostError:
                recovery_guard_aborted_before_send = True
                raise
            except asyncio.CancelledError as error:
                recovery_guard_aborted_before_send = True
                recovery_guard_control_flow_error = error
                raise
            except Exception as error:
                # Guard/storage failures are control-plane truth even when
                # their concrete class resembles a provider failure (for
                # example httpx.ConnectError or ProviderUnavailableError).
                # Envelope them before RetryPolicy sees them so no provider
                # retry token or normalization/fallback authority is minted.
                recovery_guard_aborted_before_send = True
                raise ProviderRecoveryGuardError(error) from error
            except BaseException as error:
                recovery_guard_aborted_before_send = True
                recovery_guard_control_flow_error = error
                raise

        async def execution_func():
            nonlocal provider_attempted
            attempt_kwargs = dict(kwargs)

            if call_budget is None:
                await run_recovery_pre_attempt_guard()
                provider_attempted = True
                return await provider.chat.chat(**attempt_kwargs)

            # The AE-R10 logical call deadline remains the stronger authority
            # at both sides of the R12 pre-attempt recovery fence.
            self._remaining_or_raise(call_budget, provider.name)
            try:
                await run_recovery_pre_attempt_guard()
            except ProviderRecoveryAuthorityLostError:
                # If the same logical budget expired while the guard was
                # running, deadline truth dominates recovery authority loss.
                self._remaining_or_raise(call_budget, provider.name)
                raise
            self._remaining_or_raise(call_budget, provider.name)

            async def run_attempt(remaining: float):
                nonlocal provider_attempted
                current_remaining = self._remaining_or_raise(
                    call_budget,
                    provider.name,
                )
                bounded_kwargs = dict(attempt_kwargs)
                bounded_kwargs["timeout"] = self._bounded_attempt_timeout(
                    bounded_kwargs.get("timeout"),
                    min(remaining, current_remaining),
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
            probe_lock = getattr(breaker, "_half_open_lock", None)
            half_open_probe_acquired = (
                getattr(breaker, "current_state", None)
                == CircuitBreakerState.HALF_OPEN
                and probe_lock is not None
                and probe_lock.locked()
            )

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

        except asyncio.CancelledError as error:
            current_task = asyncio.current_task()
            caller_cancelled = bool(
                current_task is not None and current_task.cancelling()
            )
            if recovery_guard_control_flow_error is error:
                # Exact guard-origin cancellation is control-plane truth and
                # occurs before the next physical provider send.
                self._release_half_open_probe_without_provider_outcome(
                    breaker,
                    probe_acquired=half_open_probe_acquired,
                )
            elif caller_cancelled:
                # Preserve canonical AE-R10 caller-cancellation semantics:
                # propagate CancelledError without recording provider failure.
                # If this call held a HALF_OPEN probe, relinquish it neutrally
                # so caller cancellation cannot strand the exclusive trial.
                self._release_half_open_probe_without_provider_outcome(
                    breaker,
                    probe_acquired=half_open_probe_acquired,
                )
            elif half_open_probe_acquired:
                # A CancelledError raised internally by provider dispatch or
                # retry backoff is not caller cancellation and is not proven
                # neutral. Fail the acquired HALF_OPEN trial closed.
                await breaker.on_failure()
            raise

        except ProviderRecoveryGuardError as guard_error:
            # RetryPolicy cannot classify the transport envelope as a provider
            # failure. Relinquish any locally acquired HALF_OPEN probe, then
            # restore the exact original guard exception at the executor API
            # boundary. Explicit chaining carries provenance to the handler
            # without requiring the original exception to accept attributes.
            self._release_half_open_probe_without_provider_outcome(
                breaker,
                probe_acquired=half_open_probe_acquired,
            )
            original_error = guard_error.original_error
            raise original_error from guard_error

        except ProviderRecoveryAuthorityLostError:
            # R12 control-plane authority loss is not a provider failure and
            # must not consume breaker, retry, or fallback authority. If
            # before_request() acquired a HALF_OPEN trial, relinquish only the
            # probe lock so a later independently-authorized call can retry it.
            self._release_half_open_probe_without_provider_outcome(
                breaker,
                probe_acquired=half_open_probe_acquired,
            )
            raise

        except ProviderDeadlineExceededError:
            # An expired caller budget is not a provider failure when no
            # request reached the provider. If an earlier attempt did run,
            # retain the existing one-failure-per-executor-call accounting.
            if provider_attempted:
                await breaker.on_failure()
            else:
                self._release_half_open_probe_without_provider_outcome(
                    breaker,
                    probe_acquired=half_open_probe_acquired,
                )
            raise

        except CircuitBreakerOpenError as e:
            if recovery_guard_aborted_before_send:
                self._release_half_open_probe_without_provider_outcome(
                    breaker,
                    probe_acquired=half_open_probe_acquired,
                )
                raise
            logger.warning(
                "Skipping provider call, circuit breaker is open.",
                provider=provider.name,
            )
            raise ProviderError(
                f"Circuit breaker is open for {provider.name}",
                provider_name=provider.name,
            ) from e

        except Exception as e:
            # A recovery guard/storage/control-plane exception before a
            # physical send is not a provider outcome and must not be
            # normalized into provider failure/fallback authority.
            if recovery_guard_aborted_before_send:
                self._release_half_open_probe_without_provider_outcome(
                    breaker,
                    probe_acquired=half_open_probe_acquired,
                )
                raise

            if provider_attempted:
                await breaker.on_failure()
            else:
                self._release_half_open_probe_without_provider_outcome(
                    breaker,
                    probe_acquired=half_open_probe_acquired,
                )

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

        except BaseException as error:
            # Non-Exception guard exits (for example GeneratorExit or a
            # BaseExceptionGroup) still must relinquish an acquired HALF_OPEN
            # probe. Exact object identity prevents unrelated exits from being
            # misclassified as recovery-control-plane truth.
            if recovery_guard_control_flow_error is error:
                self._release_half_open_probe_without_provider_outcome(
                    breaker,
                    probe_acquired=half_open_probe_acquired,
                )
            raise

    async def execute_stream(
        self,
        *,
        call_budget: ProviderCallBudget | None = None,
        timeout: float | None = None,
        first_response_timeout: float | None = None,
        stream_idle_timeout: float | None = None,
        is_semantic_progress: Callable[[GatewayStreamChunk], bool] | None = None,
        **kwargs,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Execute one stream under the shared logical-call deadline.

        Streaming never performs a provider-local retry. The handler may move
        to another provider only before the first visible chunk.
        """

        provider = kwargs.get("provider")
        breaker = await self.breaker_manager.get_breaker(provider.name)
        provider_attempted = False
        semantic_progress_seen = False

        def positive_optional(value: float | None, *, name: str) -> float | None:
            if value is None:
                return None
            normalized = float(value)
            if not math.isfinite(normalized) or normalized <= 0:
                raise ValueError(f"{name} must be finite and positive")
            return normalized

        first_response_timeout = positive_optional(
            first_response_timeout,
            name="first_response_timeout",
        )
        stream_idle_timeout = positive_optional(
            stream_idle_timeout,
            name="stream_idle_timeout",
        )
        scope_deadline: float | None = None

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
            scope_deadline = (
                None
                if first_response_timeout is None
                else time.monotonic() + first_response_timeout
            )
            try:
                while True:
                    try:
                        hard_remaining = (
                            None
                            if call_budget is None
                            else self._remaining_or_raise(
                                call_budget,
                                provider.name,
                            )
                        )
                        scope_remaining = (
                            None
                            if scope_deadline is None
                            else max(0.0, scope_deadline - time.monotonic())
                        )
                        wait_candidates = [
                            value
                            for value in (hard_remaining, scope_remaining)
                            if value is not None
                        ]
                        wait_timeout = (
                            min(wait_candidates) if wait_candidates else None
                        )
                        provider_attempted = True
                        if wait_timeout is None:
                            chunk = await stream_iterator.__anext__()
                        else:
                            next_chunk_task = asyncio.create_task(
                                stream_iterator.__anext__()
                            )
                            done, _ = await asyncio.wait(
                                {next_chunk_task},
                                timeout=wait_timeout,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if next_chunk_task not in done:
                                next_chunk_task.cancel()
                                await asyncio.gather(
                                    next_chunk_task,
                                    return_exceptions=True,
                                )
                                next_chunk_task = None
                                if (
                                    call_budget is not None
                                    and call_budget.remaining_seconds(
                                        now_monotonic=time.monotonic()
                                    )
                                    <= 0
                                ):
                                    raise ProviderDeadlineExceededError(
                                        "Provider stream deadline exceeded.",
                                        provider_name=provider.name,
                                    )
                                if not semantic_progress_seen:
                                    assert first_response_timeout is not None
                                    raise ProviderFirstResponseTimeoutError(
                                        "Provider first response timed out.",
                                        provider_name=provider.name,
                                        timeout_seconds=first_response_timeout,
                                    )
                                assert stream_idle_timeout is not None
                                raise ProviderStreamIdleTimeoutError(
                                    "Provider stream became idle.",
                                    provider_name=provider.name,
                                    timeout_seconds=stream_idle_timeout,
                                )
                            chunk = await next_chunk_task
                            next_chunk_task = None
                            if call_budget is not None:
                                self._remaining_or_raise(
                                    call_budget,
                                    provider.name,
                                )
                    except StopAsyncIteration:
                        next_chunk_task = None
                        break

                    semantic_progress = (
                        True
                        if is_semantic_progress is None
                        else bool(is_semantic_progress(chunk))
                    )
                    if semantic_progress:
                        semantic_progress_seen = True
                        scope_deadline = (
                            None
                            if stream_idle_timeout is None
                            else time.monotonic() + stream_idle_timeout
                        )

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

        except (
            ProviderFirstResponseTimeoutError,
            ProviderStreamIdleTimeoutError,
        ):
            if provider_attempted:
                await breaker.on_failure()
            raise

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
