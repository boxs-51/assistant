from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.provider.exceptions import (
    PROVIDER_ERROR,
    PROVIDER_FALLBACK_EXHAUSTED,
    PROVIDER_RATE_LIMITED,
    PROVIDER_UNAVAILABLE,
    NoAvailableProviderError,
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from se.src.provider.executor import ProviderExecutor
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.provider.retry_contracts import ProviderCallBudget
from se.src.runtimes.provider.runtime import ProviderRuntime


class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, model=None, metadata=None):
        return list(self.providers)


class _Provider:
    def __init__(self, name, *, probe=True):
        self.name = name
        self.probe = probe
        self.probe_calls = 0
        self.probe_timeouts = []

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.probe_calls += 1
        self.probe_timeouts.append(timeout)
        if isinstance(self.probe, BaseException):
            raise self.probe
        return self.probe


class _StreamExecutor:
    def __init__(self, outcomes, *, max_retries=2):
        self.retry_policy = SimpleNamespace(max_retries=max_retries)
        self.outcomes = {
            name: list(items)
            for name, items in outcomes.items()
        }
        self.provider_calls = []
        self.budgets = []
        self.timeouts = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute_stream(
        self,
        *,
        provider,
        call_budget,
        timeout,
        **kwargs,
    ):
        self.provider_calls.append(provider.name)
        self.budgets.append(call_budget)
        self.timeouts.append(timeout)
        for item in self.outcomes[provider.name]:
            if isinstance(item, BaseException):
                raise item
            yield item


def _handler(providers, executor, *, timeout=10.0):
    return ChatExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        timeout=timeout,
    )


@pytest.mark.asyncio
async def test_r10_g_pre_first_chunk_failure_falls_back_with_same_budget(
    monkeypatch,
):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    p1 = _Provider("p1")
    p2 = _Provider("p2")
    first_error = ProviderUnavailableError(
        "pre-stream failure",
        provider_name="p1",
    )
    executor = _StreamExecutor(
        {
            "p1": [first_error],
            "p2": ["p2-chunk"],
        }
    )

    chunks = [
        chunk
        async for chunk in _handler(
            [p1, p2],
            executor,
            timeout=10.0,
        ).stream_with_fallback(
            object(),
            {"model": "logical-model"},
        )
    ]

    assert chunks == ["p2-chunk"]
    assert executor.provider_calls == ["p1", "p2"]
    assert executor.budgets[0] is executor.budgets[1]
    assert executor.budgets[1].retries_used == 0
    assert p1.probe_calls == 1
    assert p2.probe_calls == 1


@pytest.mark.asyncio
async def test_r10_g_post_first_chunk_failure_never_replays_or_falls_back(
    monkeypatch,
):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    p1 = _Provider("p1")
    p2 = _Provider("p2")
    mid_error = ProviderUnavailableError(
        "stream interrupted",
        provider_name="p1",
    )
    executor = _StreamExecutor(
        {
            "p1": ["first-visible", mid_error],
            "p2": ["must-not-run"],
        }
    )
    chunks = []

    with pytest.raises(ProviderUnavailableError) as raised:
        async for chunk in _handler(
            [p1, p2],
            executor,
            timeout=10.0,
        ).stream_with_fallback(
            object(),
            {"model": "logical-model"},
        ):
            chunks.append(chunk)

    assert chunks == ["first-visible"]
    assert raised.value is mid_error
    assert raised.value.code == PROVIDER_UNAVAILABLE
    assert raised.value.provider_name == "p1"
    assert executor.provider_calls == ["p1"]
    assert p2.probe_calls == 0


@pytest.mark.asyncio
async def test_r10_g_stream_exhaustion_preserves_last_provider_and_cause(
    monkeypatch,
):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    p1_error = ProviderUnavailableError(
        "p1 probe failed",
        provider_name="p1",
    )
    p1 = _Provider("p1", probe=p1_error)
    p2 = _Provider("p2")
    last_error = ProviderRateLimitError(
        "p2 quota",
        provider_name="p2",
        status_code=429,
        error_code="RESOURCE_EXHAUSTED",
    )
    executor = _StreamExecutor(
        {
            "p1": [],
            "p2": [last_error],
        }
    )

    with pytest.raises(NoAvailableProviderError) as raised:
        async for _ in _handler(
            [p1, p2],
            executor,
            timeout=10.0,
        ).stream_with_fallback(
            object(),
            {"model": "logical-model"},
        ):
            pass

    error = raised.value
    assert error.code == PROVIDER_FALLBACK_EXHAUSTED
    assert error.provider_name == "p2"
    assert error.status_code == 429
    assert error.error_code == "RESOURCE_EXHAUSTED"
    assert error.__cause__ is last_error
    assert executor.provider_calls == ["p2"]
    assert executor.budgets[0].retries_used == 0


@pytest.mark.asyncio
async def test_r10_g_direct_stream_uses_configured_provider_deadline(
    monkeypatch,
):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 50.0,
    )
    provider = _Provider("p1")
    executor = _StreamExecutor({"p1": ["ok"]})

    chunks = [
        chunk
        async for chunk in _handler(
            [provider],
            executor,
            timeout=4.0,
        ).stream_with_fallback(
            object(),
            {"model": "logical-model"},
        )
    ]

    assert chunks == ["ok"]
    assert provider.probe_timeouts == [pytest.approx(4.0)]
    assert executor.budgets[0].deadline_monotonic == pytest.approx(54.0)
    assert executor.timeouts == [4.0]


@pytest.mark.asyncio
async def test_r10_g_caller_deadline_bounds_stream_probe_and_budget(
    monkeypatch,
):
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    provider = _Provider("p1")
    executor = _StreamExecutor({"p1": ["ok"]})

    chunks = [
        chunk
        async for chunk in _handler(
            [provider],
            executor,
            timeout=60.0,
        ).stream_with_fallback(
            object(),
            {"model": "logical-model"},
            deadline_monotonic=105.0,
        )
    ]

    assert chunks == ["ok"]
    assert provider.probe_timeouts == [pytest.approx(5.0)]
    assert executor.budgets[0].deadline_monotonic == pytest.approx(105.0)


class _Breaker:
    def __init__(self):
        self.failures = 0
        self.successes = 0

    async def is_open(self):
        return False

    async def before_request(self):
        return None

    async def on_success(self):
        self.successes += 1

    async def on_failure(self):
        self.failures += 1


class _BreakerManager:
    def __init__(self):
        self.breakers = {}

    async def get_breaker(self, provider_name):
        return self.breakers.setdefault(provider_name, _Breaker())


class _RealStreamChat:
    def __init__(self, *, delay=0.0):
        self.delay = delay
        self.timeouts = []

    async def chat_stream(self, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        if self.delay:
            await asyncio.sleep(self.delay)
        yield "real-chunk"


@pytest.mark.asyncio
async def test_r10_g_executor_bounds_provider_stream_timeout_by_remaining():
    manager = _BreakerManager()
    executor = ProviderExecutor(manager, max_retries=0)
    chat = _RealStreamChat()
    provider = SimpleNamespace(name="p1", chat=chat)
    now = time.monotonic()
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=now,
        timeout_seconds=1.0,
        max_retries=0,
    )

    chunks = [
        chunk
        async for chunk in executor.execute_stream(
            provider=provider,
            http_client=object(),
            body={"model": "logical-model"},
            timeout=60.0,
            call_budget=budget,
        )
    ]

    assert chunks == ["real-chunk"]
    assert len(chat.timeouts) == 1
    assert chat.timeouts[0] is not None
    assert 0 < chat.timeouts[0] <= 1.0


@pytest.mark.asyncio
async def test_r10_g_provider_internal_timeout_is_not_logical_deadline():
    class _InternalTimeoutChat:
        async def chat_stream(self, **kwargs):
            raise TimeoutError("provider internal timeout")
            yield "unreachable"

    manager = _BreakerManager()
    executor = ProviderExecutor(manager, max_retries=0)
    provider = SimpleNamespace(
        name="p1",
        chat=_InternalTimeoutChat(),
    )
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=1.0,
        max_retries=0,
    )

    with pytest.raises(ProviderError) as raised:
        async for _ in executor.execute_stream(
            provider=provider,
            http_client=object(),
            body={"model": "logical-model"},
            timeout=60.0,
            call_budget=budget,
        ):
            pass

    assert raised.value.code == PROVIDER_ERROR
    assert not isinstance(raised.value, ProviderDeadlineExceededError)


@pytest.mark.asyncio
async def test_r10_g_executor_enforces_total_stream_deadline():
    manager = _BreakerManager()
    executor = ProviderExecutor(manager, max_retries=0)
    chat = _RealStreamChat(delay=1.0)
    provider = SimpleNamespace(name="p1", chat=chat)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.02,
        max_retries=0,
    )

    with pytest.raises(ProviderDeadlineExceededError):
        async for _ in executor.execute_stream(
            provider=provider,
            http_client=object(),
            body={"model": "logical-model"},
            timeout=60.0,
            call_budget=budget,
        ):
            pass

    breaker = await manager.get_breaker("p1")
    assert breaker.failures == 1
    assert breaker.successes == 0


class _PublishingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_r10_g_provider_runtime_exposes_stable_provider_failure_metadata():
    provider_error = ProviderRateLimitError(
        "quota",
        provider_name="p1",
        status_code=429,
        error_code="RESOURCE_EXHAUSTED",
    )

    class _FailingHandler:
        async def execute_with_fallback(self, http_client, body):
            raise provider_error

    runtime = ProviderRuntime(circuit_breaker_manager=object())
    runtime.chat_handler = _FailingHandler()
    runtime._http_client = object()
    runtime.event_bus = _PublishingBus()

    await runtime._handle_execute_chat(
        BaseEvent(
            event_name="provider.chat.execute",
            session_id="session-r10-g",
            turn_id="turn-r10-g",
            payload={
                "request_body": {
                    "model": "logical-model",
                    "config": {"stream": False},
                }
            },
        )
    )

    assert len(runtime.event_bus.events) == 1
    event = runtime.event_bus.events[0]
    assert event.event_name == "provider.failed"
    assert event.payload["status_code"] == 500
    assert event.payload["error_code"] == PROVIDER_RATE_LIMITED
    assert event.payload["failure_domain"] == "PROVIDER"
    assert event.payload["retryable"] is True
    assert event.payload["provider"] == "p1"
    assert provider_error.error_code == "RESOURCE_EXHAUSTED"
