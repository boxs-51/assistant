from __future__ import annotations

from types import SimpleNamespace
import asyncio
import time

import pytest

from se.src.domain.schemas.event import BaseEvent
from se.src.provider.exceptions import (
    PROVIDER_UNAVAILABLE,
    ProviderAuthenticationError,
    ProviderUnavailableError,
    ResponseValidationError,
)
from se.src.provider.executor import ProviderExecutor
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.provider.policies.retry import RetryPolicy
from se.src.provider.retry_contracts import ProviderCallBudget
from se.src.runtimes.provider.runtime import ProviderRuntime


class _Breaker:
    def __init__(self, *, opened: bool = False):
        self.opened = opened
        self.failures = 0
        self.successes = 0

    async def is_open(self):
        return self.opened

    async def before_request(self):
        if self.opened:
            from se.src.circuit_breaker import CircuitBreakerOpenError
            raise CircuitBreakerOpenError("open")

    async def on_success(self):
        self.successes += 1

    async def on_failure(self):
        self.failures += 1


class _BreakerManager:
    def __init__(self, *, open_names=()):
        self.open_names = set(open_names)
        self.breakers = {}

    async def get_breaker(self, provider_name):
        if provider_name not in self.breakers:
            self.breakers[provider_name] = _Breaker(
                opened=provider_name in self.open_names
            )
        return self.breakers[provider_name]


class _Chat:
    def __init__(self, owner):
        self.owner = owner

    async def chat(self, **kwargs):
        self.owner.attempts += 1
        outcome = self.owner.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Provider:
    def __init__(self, name, outcomes):
        self.name = name
        self.outcomes = list(outcomes)
        self.attempts = 0
        self.probe_calls = 0
        self.chat = _Chat(self)

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.probe_calls += 1
        return True


class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, model=None, metadata=None):
        return list(self.providers)


def _handler(providers, executor, *, timeout=30.0):
    return ChatExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=executor.breaker_manager,
        timeout=timeout,
    )


@pytest.mark.asyncio
async def test_r10_h_global_retry_ceiling_does_not_reset_across_three_providers(
    monkeypatch,
):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        no_sleep,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )

    providers = [
        _Provider(
            "p1",
            [
                ProviderUnavailableError("p1 initial", provider_name="p1"),
                ProviderUnavailableError("p1 retry 1", provider_name="p1"),
                ProviderUnavailableError("p1 retry 2", provider_name="p1"),
            ],
        ),
        _Provider(
            "p2",
            [ProviderUnavailableError("p2 initial", provider_name="p2")],
        ),
        _Provider("p3", ["ok"]),
    ]
    manager = _BreakerManager()
    executor = ProviderExecutor(
        manager,
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=30.0,
        max_retries=2,
    )
    handler = _handler(providers, executor)
    handler._new_call_budget = lambda _deadline=None: budget

    result = await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "ok"
    assert [provider.attempts for provider in providers] == [3, 1, 1]
    assert sum(provider.attempts for provider in providers) == 5
    assert budget.retries_used == 2
    assert budget.retries_remaining == 0


@pytest.mark.asyncio
async def test_r10_h_circuit_open_provider_is_skipped_without_retry_charge():
    p1 = _Provider("p1", [])
    p2 = _Provider("p2", ["ok"])
    manager = _BreakerManager(open_names={"p1"})
    executor = ProviderExecutor(
        manager,
        retry_policy=RetryPolicy(max_retries=2),
    )
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=30.0,
        max_retries=2,
    )
    handler = _handler([p1, p2], executor)
    handler._new_call_budget = lambda _deadline=None: budget

    result = await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "ok"
    assert p1.probe_calls == 0
    assert p1.attempts == 0
    assert p2.probe_calls == 1
    assert p2.attempts == 1
    assert budget.retries_used == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProviderAuthenticationError(
            "bad credentials",
            provider_name="p1",
            status_code=401,
        ),
        ResponseValidationError(
            "invalid provider response",
            provider_name="p1",
        ),
    ],
)
async def test_r10_h_nonretryable_provider_failures_never_schedule_retry(
    monkeypatch,
    error,
):
    async def forbidden_sleep(_delay):
        raise AssertionError("non-retryable failure must not back off")

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        forbidden_sleep,
    )
    attempts = 0
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=30.0,
        max_retries=3,
    )

    async def fail():
        nonlocal attempts
        attempts += 1
        raise error

    with pytest.raises(type(error)) as raised:
        await RetryPolicy(max_retries=99).apply(
            fail,
            "p1",
            call_budget=budget,
        )

    assert raised.value is error
    assert attempts == 1
    assert budget.retries_used == 0


class _PublishingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class _Chunk:
    def model_dump(self):
        return {"delta": "visible"}

    def to_sse(self):
        return "data: visible\n\n"


@pytest.mark.asyncio
async def test_r10_h_public_stream_visible_failure_sequence_is_terminal_no_replay():
    provider_error = ProviderUnavailableError(
        "stream failed after visible chunk",
        provider_name="p1",
    )

    class _Handler:
        async def stream_with_fallback(self, http_client, body):
            yield _Chunk()
            raise provider_error

    runtime = ProviderRuntime(circuit_breaker_manager=object())
    runtime.chat_handler = _Handler()
    runtime._http_client = object()
    runtime.event_bus = _PublishingBus()

    await runtime._handle_execute_chat(
        BaseEvent(
            event_name="provider.chat.execute",
            session_id="session-r10-h",
            turn_id="turn-r10-h",
            payload={
                "request_body": {
                    "model": "logical-model",
                    "config": {"stream": True},
                }
            },
        )
    )

    assert [event.event_name for event in runtime.event_bus.events] == [
        "provider.stream.chunk_emitted",
        "provider.failed",
    ]
    assert not any(
        event.event_name == "provider.stream.completed"
        for event in runtime.event_bus.events
    )
    failure = runtime.event_bus.events[-1].payload
    assert failure["error_code"] == PROVIDER_UNAVAILABLE
    assert failure["failure_domain"] == "PROVIDER"
    assert failure["retryable"] is True
    assert failure["provider"] == "p1"



class _FailingChunkBus:
    def __init__(self, closed: asyncio.Event):
        self.events = []
        self.closed = closed
        self.closed_before_failure_event = None

    async def publish(self, event):
        self.events.append(event.event_name)
        if event.event_name == "provider.stream.chunk_emitted":
            raise RuntimeError("downstream chunk publication failed")
        if event.event_name == "provider.failed":
            self.closed_before_failure_event = self.closed.is_set()


class _BlockingChunkBus:
    def __init__(self):
        self.events = []
        self.chunk_publish_started = asyncio.Event()

    async def publish(self, event):
        self.events.append(event.event_name)
        if event.event_name == "provider.stream.chunk_emitted":
            self.chunk_publish_started.set()
            await asyncio.Event().wait()


class _CloseAwareStreamChat:
    def __init__(self, closed: asyncio.Event):
        self.closed = closed

    async def chat_stream(self, **kwargs):
        try:
            yield _Chunk()
            await asyncio.Event().wait()
        finally:
            self.closed.set()


class _CloseAwareProvider:
    def __init__(self, name: str, closed: asyncio.Event):
        self.name = name
        self.chat = _CloseAwareStreamChat(closed)

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        return True


def _runtime_with_real_stream(closed: asyncio.Event, event_bus):
    manager = _BreakerManager()
    provider = _CloseAwareProvider("p1", closed)
    executor = ProviderExecutor(
        manager,
        retry_policy=RetryPolicy(max_retries=0),
    )
    handler = ChatExecutionHandler(
        providers={"p1": provider},
        routing_policy=_Routing([provider]),
        executor=executor,
        circuit_breaker_manager=manager,
        timeout=30.0,
    )
    runtime = ProviderRuntime(circuit_breaker_manager=manager)
    runtime.chat_handler = handler
    runtime._http_client = object()
    runtime.event_bus = event_bus
    return runtime


@pytest.mark.asyncio
async def test_r10_h_runtime_closes_nested_provider_stream_when_chunk_publish_fails():
    closed = asyncio.Event()
    bus = _FailingChunkBus(closed)
    runtime = _runtime_with_real_stream(closed, bus)

    await runtime._handle_execute_chat(
        BaseEvent(
            event_name="provider.chat.execute",
            session_id="session-r10-h-publish-fail",
            turn_id="turn-r10-h-publish-fail",
            payload={
                "request_body": {
                    "model": "logical-model",
                    "config": {"stream": True},
                }
            },
        )
    )

    assert closed.is_set()
    assert bus.closed_before_failure_event is True
    assert bus.events == [
        "provider.stream.chunk_emitted",
        "provider.failed",
    ]


@pytest.mark.asyncio
async def test_r10_h_runtime_cancellation_closes_nested_provider_stream_before_return():
    closed = asyncio.Event()
    bus = _BlockingChunkBus()
    runtime = _runtime_with_real_stream(closed, bus)
    task = asyncio.create_task(
        runtime._handle_execute_chat(
            BaseEvent(
                event_name="provider.chat.execute",
                session_id="session-r10-h-cancel",
                turn_id="turn-r10-h-cancel",
                payload={
                    "request_body": {
                        "model": "logical-model",
                        "config": {"stream": True},
                    }
                },
            )
        )
    )

    await bus.chunk_publish_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert closed.is_set()
    assert bus.events == ["provider.stream.chunk_emitted"]
