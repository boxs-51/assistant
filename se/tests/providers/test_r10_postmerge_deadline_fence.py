from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from se.src.provider.exceptions import ProviderDeadlineExceededError
from se.src.provider.executor import ProviderExecutor
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from se.src.provider.policies.retry import RetryPolicy
from se.src.provider.retry_contracts import ProviderCallBudget


class _Breaker:
    def __init__(self):
        self.before_calls = 0
        self.success_calls = 0
        self.failure_calls = 0

    async def is_open(self):
        return False

    async def before_request(self):
        self.before_calls += 1

    async def on_success(self):
        self.success_calls += 1

    async def on_failure(self):
        self.failure_calls += 1


class _BreakerManager:
    def __init__(self, breaker):
        self.breaker = breaker

    async def get_breaker(self, provider_name):
        return self.breaker


class _BlockingChat:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.timeouts = []

    async def chat(self, **kwargs):
        self.calls += 1
        self.timeouts.append(kwargs.get("timeout"))
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError("unreachable")


class _Provider:
    def __init__(self, name: str, chat):
        self.name = name
        self.chat = chat


def _executor(breaker):
    return ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=2),
    )


@pytest.mark.asyncio
async def test_postmerge_r10_execute_hard_fences_provider_await_and_cancels_child():
    breaker = _Breaker()
    chat = _BlockingChat()
    provider = _Provider("p1", chat)
    executor = _executor(breaker)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.03,
        max_retries=2,
    )

    with pytest.raises(ProviderDeadlineExceededError):
        await asyncio.wait_for(
            executor.execute(
                provider=provider,
                http_client=object(),
                body={"model": "logical-model"},
                timeout=30.0,
                call_budget=budget,
            ),
            timeout=0.20,
        )

    assert chat.calls == 1
    assert chat.cancelled.is_set()
    assert breaker.before_calls == 1
    assert breaker.success_calls == 0
    assert breaker.failure_calls == 1
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_postmerge_r10_execute_generic_hard_fences_operation_and_cancels_child():
    breaker = _Breaker()
    provider = SimpleNamespace(name="p1")
    executor = _executor(breaker)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.03,
        max_retries=2,
    )
    calls = 0
    cancelled = asyncio.Event()

    async def operation(attempt_timeout):
        nonlocal calls
        calls += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    with pytest.raises(ProviderDeadlineExceededError):
        await asyncio.wait_for(
            executor.execute_generic(
                provider,
                operation,
                call_budget=budget,
                timeout=30.0,
            ),
            timeout=0.20,
        )

    assert calls == 1
    assert cancelled.is_set()
    assert breaker.before_calls == 1
    assert breaker.success_calls == 0
    assert breaker.failure_calls == 1
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_postmerge_r10_late_success_after_deadline_is_rejected(monkeypatch):
    clock = {"now": 100.0}
    breaker = _Breaker()

    class _LateChat:
        async def chat(self, **kwargs):
            clock["now"] = 101.0
            return "late-success"

    provider = _Provider("p1", _LateChat())
    executor = _executor(breaker)
    budget = ProviderCallBudget(
        deadline_monotonic=100.5,
        max_retries=2,
    )

    monkeypatch.setattr(
        "se.src.provider.executor.time.monotonic",
        lambda: clock["now"],
    )

    with pytest.raises(ProviderDeadlineExceededError):
        await executor.execute(
            provider=provider,
            http_client=object(),
            body={"model": "logical-model"},
            timeout=30.0,
            call_budget=budget,
        )

    assert breaker.before_calls == 1
    assert breaker.success_calls == 0
    assert breaker.failure_calls == 1
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_postmerge_r10_caller_cancellation_remains_cancelled_error():
    breaker = _Breaker()
    chat = _BlockingChat()
    provider = _Provider("p1", chat)
    executor = _executor(breaker)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=10.0,
        max_retries=2,
    )

    task = asyncio.create_task(
        executor.execute(
            provider=provider,
            http_client=object(),
            body={"model": "logical-model"},
            timeout=30.0,
            call_budget=budget,
        )
    )
    await chat.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert chat.cancelled.is_set()
    assert breaker.success_calls == 0
    assert breaker.failure_calls == 0
    assert budget.retries_used == 0



class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, model=None, metadata=None):
        return list(self.providers)


class _ProbeProvider:
    def __init__(self, name: str, *, capability_result=True, block=False):
        self.name = name
        self.capability_result = capability_result
        self.block = block
        self.probe_calls = 0
        self.probe_started = asyncio.Event()
        self.probe_cancelled = asyncio.Event()
        self.chat = SimpleNamespace()
        self.embeddings = SimpleNamespace()

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.probe_calls += 1
        self.probe_started.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.probe_cancelled.set()
                raise
        return self.capability_result


class _NoAttemptExecutor:
    def __init__(self, *, result="ok", max_retries=2):
        self.retry_policy = SimpleNamespace(max_retries=max_retries)
        self.result = result
        self.execute_calls = 0
        self.generic_calls = 0

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, **kwargs):
        self.execute_calls += 1
        return self.result

    async def execute_generic(self, *args, **kwargs):
        self.generic_calls += 1
        return self.result


def _chat_handler(providers, executor, *, timeout=0.03):
    return ChatExecutionHandler(
        providers={p.name: p for p in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        timeout=timeout,
    )


def _embedding_handler(providers, executor, *, timeout=0.03):
    return EmbeddingExecutionHandler(
        providers={p.name: p for p in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        timeout=timeout,
    )


@pytest.mark.asyncio
async def test_postmerge_r10_chat_probe_is_hard_fenced_before_executor_attempt():
    p1 = _ProbeProvider("p1", block=True)
    p2 = _ProbeProvider("p2")
    executor = _NoAttemptExecutor(max_retries=2)
    handler = _chat_handler([p1, p2], executor, timeout=0.03)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.03,
        max_retries=2,
    )
    handler._new_call_budget = lambda *_args, **_kwargs: budget

    with pytest.raises(ProviderDeadlineExceededError):
        await asyncio.wait_for(
            handler.execute_with_fallback(
                object(),
                {"model": "logical-model"},
            ),
            timeout=0.20,
        )

    assert p1.probe_calls == 1
    assert p1.probe_cancelled.is_set()
    assert p2.probe_calls == 0
    assert executor.execute_calls == 0
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_postmerge_r10_chat_stream_probe_is_hard_fenced_before_stream_attempt():
    p1 = _ProbeProvider("p1", block=True)
    p2 = _ProbeProvider("p2")
    executor = _NoAttemptExecutor(max_retries=2)
    handler = _chat_handler([p1, p2], executor, timeout=0.03)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.03,
        max_retries=2,
    )
    handler._new_call_budget = lambda *_args, **_kwargs: budget

    stream = handler.stream_with_fallback(
        object(),
        {"model": "logical-model"},
    )
    with pytest.raises(ProviderDeadlineExceededError):
        await asyncio.wait_for(stream.__anext__(), timeout=0.20)

    assert p1.probe_calls == 1
    assert p1.probe_cancelled.is_set()
    assert p2.probe_calls == 0
    assert executor.execute_calls == 0
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_postmerge_r10_embedding_probe_is_hard_fenced_before_generic_attempt():
    p1 = _ProbeProvider("p1", block=True)
    p2 = _ProbeProvider("p2")
    executor = _NoAttemptExecutor(max_retries=2)
    handler = _embedding_handler([p1, p2], executor, timeout=0.03)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=0.03,
        max_retries=2,
    )
    handler._new_call_budget = lambda *_args, **_kwargs: budget

    with pytest.raises(ProviderDeadlineExceededError):
        await asyncio.wait_for(
            handler.execute(
                object(),
                {"model": "logical-model"},
            ),
            timeout=0.20,
        )

    assert p1.probe_calls == 1
    assert p1.probe_cancelled.is_set()
    assert p2.probe_calls == 0
    assert executor.generic_calls == 0
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_postmerge_r10_probe_caller_cancellation_remains_authoritative():
    p1 = _ProbeProvider("p1", block=True)
    executor = _NoAttemptExecutor(max_retries=2)
    handler = _chat_handler([p1], executor, timeout=10.0)
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=time.monotonic(),
        timeout_seconds=10.0,
        max_retries=2,
    )
    handler._new_call_budget = lambda *_args, **_kwargs: budget

    task = asyncio.create_task(
        handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
        )
    )
    await p1.probe_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert p1.probe_cancelled.is_set()
    assert executor.execute_calls == 0
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_postmerge_r10_successful_bounded_probe_preserves_fallback():
    p1 = _ProbeProvider("p1", capability_result=False)
    p2 = _ProbeProvider("p2", capability_result=True)
    executor = _NoAttemptExecutor(result="p2-ok", max_retries=2)
    handler = _chat_handler([p1, p2], executor, timeout=1.0)

    result = await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "p2-ok"
    assert p1.probe_calls == 1
    assert p2.probe_calls == 1
    assert executor.execute_calls == 1
