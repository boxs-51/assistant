from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from se.src.infrastructure.config.schemas import ProviderSettings
from se.src.provider.exceptions import (
    PROVIDER_FIRST_RESPONSE_TIMEOUT,
    PROVIDER_STREAM_IDLE_TIMEOUT,
    ProviderDeadlineExceededError,
    ProviderFirstResponseTimeoutError,
    ProviderStreamIdleTimeoutError,
)
from se.src.provider.executor import ProviderExecutor
from se.src.provider.retry_contracts import ProviderCallBudget
from se.src.runtimes.provider.runtime import _provider_failure_payload


class _Breaker:
    def __init__(self) -> None:
        self.failures = 0
        self.successes = 0

    async def is_open(self) -> bool:
        return False

    async def before_request(self) -> None:
        return None

    async def on_success(self) -> None:
        self.successes += 1

    async def on_failure(self) -> None:
        self.failures += 1


class _BreakerManager:
    def __init__(self) -> None:
        self.breaker = _Breaker()

    async def get_breaker(self, provider_name: str) -> _Breaker:
        return self.breaker


def _budget(seconds: float = 1.0) -> ProviderCallBudget:
    return ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=seconds,
        max_retries=0,
    )


@pytest.mark.parametrize(
    "field",
    [
        "provider_first_response_timeout_seconds",
        "provider_stream_idle_timeout_seconds",
    ],
)
def test_ubq5b_timeout_config_is_optional_positive(field: str) -> None:
    assert getattr(ProviderSettings(), field) is None
    with pytest.raises(ValidationError):
        ProviderSettings(**{field: 0})


@pytest.mark.asyncio
async def test_ubq5b_first_response_timeout_is_scoped_and_breaker_failure() -> None:
    class _Chat:
        async def chat_stream(self, **kwargs):
            await asyncio.Event().wait()
            yield "unreachable"

    manager = _BreakerManager()
    executor = ProviderExecutor(manager, max_retries=0)
    provider = SimpleNamespace(name="p1", chat=_Chat())

    with pytest.raises(ProviderFirstResponseTimeoutError) as raised:
        async for _ in executor.execute_stream(
            provider=provider,
            call_budget=_budget(),
            first_response_timeout=0.01,
            stream_idle_timeout=0.5,
            is_semantic_progress=lambda chunk: bool(chunk),
        ):
            pass

    error = raised.value
    assert error.code == PROVIDER_FIRST_RESPONSE_TIMEOUT
    assert error.timeout_scope == "provider_first_response"
    assert error.timeout_seconds == pytest.approx(0.01)
    assert manager.breaker.failures == 1
    assert manager.breaker.successes == 0


@pytest.mark.asyncio
async def test_ubq5b_nonsemantic_chunk_does_not_satisfy_first_response() -> None:
    class _Chat:
        async def chat_stream(self, **kwargs):
            yield "metadata-only"
            await asyncio.Event().wait()
            yield "unreachable"

    manager = _BreakerManager()
    executor = ProviderExecutor(manager, max_retries=0)
    provider = SimpleNamespace(name="p1", chat=_Chat())
    stream = executor.execute_stream(
        provider=provider,
        call_budget=_budget(),
        first_response_timeout=0.02,
        stream_idle_timeout=0.5,
        is_semantic_progress=lambda chunk: chunk == "semantic",
    )

    assert await stream.__anext__() == "metadata-only"
    with pytest.raises(ProviderFirstResponseTimeoutError):
        await stream.__anext__()
    assert manager.breaker.failures == 1


@pytest.mark.asyncio
async def test_ubq5b_semantic_progress_arms_stream_idle_timeout() -> None:
    class _Chat:
        async def chat_stream(self, **kwargs):
            yield "semantic"
            await asyncio.Event().wait()
            yield "unreachable"

    manager = _BreakerManager()
    executor = ProviderExecutor(manager, max_retries=0)
    provider = SimpleNamespace(name="p1", chat=_Chat())
    stream = executor.execute_stream(
        provider=provider,
        call_budget=_budget(),
        first_response_timeout=0.5,
        stream_idle_timeout=0.01,
        is_semantic_progress=lambda chunk: chunk == "semantic",
    )

    assert await stream.__anext__() == "semantic"
    with pytest.raises(ProviderStreamIdleTimeoutError) as raised:
        await stream.__anext__()

    error = raised.value
    assert error.code == PROVIDER_STREAM_IDLE_TIMEOUT
    assert error.timeout_scope == "provider_stream_idle"
    assert error.timeout_seconds == pytest.approx(0.01)
    assert manager.breaker.failures == 1


@pytest.mark.asyncio
async def test_ubq5b_provider_call_deadline_dominates_first_response() -> None:
    class _Chat:
        async def chat_stream(self, **kwargs):
            await asyncio.Event().wait()
            yield "unreachable"

    manager = _BreakerManager()
    executor = ProviderExecutor(manager, max_retries=0)
    provider = SimpleNamespace(name="p1", chat=_Chat())

    with pytest.raises(ProviderDeadlineExceededError):
        async for _ in executor.execute_stream(
            provider=provider,
            call_budget=_budget(0.01),
            first_response_timeout=0.5,
            stream_idle_timeout=0.5,
            is_semantic_progress=lambda chunk: bool(chunk),
        ):
            pass
    assert manager.breaker.failures == 1


def test_ubq5b_direct_failure_payload_preserves_timeout_truth() -> None:
    error = ProviderStreamIdleTimeoutError(
        "idle",
        provider_name="p1",
        timeout_seconds=2.5,
    )

    payload = _provider_failure_payload(error, status_code=504)

    assert payload["error_code"] == PROVIDER_STREAM_IDLE_TIMEOUT
    assert payload["failure_domain"] == "PROVIDER"
    assert payload["timeout_scope"] == "provider_stream_idle"
    assert payload["timeout_seconds"] == pytest.approx(2.5)
    assert payload["provider"] == "p1"
