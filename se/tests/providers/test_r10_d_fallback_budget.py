from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from se.src.provider.exceptions import (
    PROVIDER_DEADLINE_EXCEEDED,
    PROVIDER_FALLBACK_EXHAUSTED,
    NoAvailableProviderError,
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderRateLimitError,
)
from se.src.provider.policies.retry import RetryPolicy
from se.src.provider.retry_contracts import (
    ProviderRetryHint,
    ProviderRetryHintSource,
)
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.provider.handlers.embedding_handler import EmbeddingExecutionHandler


class _Clock:
    def __init__(self, values):
        self.values = list(values)
        self.last = self.values[-1]

    def __call__(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last


class _Routing:
    def __init__(self, providers):
        self.providers = providers

    def get_fallback_chain(self, model=None, metadata=None):
        return list(self.providers)


class _Provider:
    def __init__(self, name):
        self.name = name
        self.probe_timeouts = []

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.probe_timeouts.append(timeout)
        return True


class _Executor:
    def __init__(self, outcomes, *, max_retries=2):
        self.retry_policy = SimpleNamespace(max_retries=max_retries)
        self.outcomes = list(outcomes)
        self.budgets = []
        self.retries_seen = []
        self.provider_calls = []

    async def is_provider_healthy(self, provider_name):
        return True

    def _next(self, provider, call_budget):
        self.provider_calls.append(provider.name)
        self.budgets.append(call_budget)
        self.retries_seen.append(call_budget.retries_used)
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            return outcome(call_budget)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def execute(self, *, provider, call_budget, **kwargs):
        return self._next(provider, call_budget)

    async def execute_generic(
        self,
        provider,
        execution_callable,
        *,
        call_budget,
        timeout=None,
    ):
        return self._next(provider, call_budget)


class _BreakerManager:
    pass


def _chat_handler(providers, executor, *, timeout=10.0):
    return ChatExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=_BreakerManager(),
        timeout=timeout,
    )


def _embedding_handler(providers, executor, *, timeout=10.0):
    return EmbeddingExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=_BreakerManager(),
        timeout=timeout,
    )


@pytest.mark.asyncio
async def test_r10_d_chat_reuses_one_budget_and_preserves_consumed_retry(
    monkeypatch,
):
    providers = [_Provider("p1"), _Provider("p2")]

    def consume_then_fail(call_budget):
        assert call_budget.try_consume_retry() is True
        raise ProviderError("p1 failed", provider_name="p1")

    executor = _Executor([consume_then_fail, "ok"], max_retries=2)
    clock = _Clock([100.0, 100.5, 101.0])
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        clock,
    )

    result = await _chat_handler(
        providers,
        executor,
        timeout=10.0,
    ).execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "ok"
    assert executor.provider_calls == ["p1", "p2"]
    assert len(executor.budgets) == 2
    assert executor.budgets[0] is executor.budgets[1]
    assert executor.retries_seen == [0, 1]
    assert executor.budgets[1].retries_used == 1
    assert providers[0].probe_timeouts == [9.5]
    assert providers[1].probe_timeouts == [9.0]


@pytest.mark.asyncio
async def test_r10_d_provider_transition_does_not_charge_retry_token(
    monkeypatch,
):
    providers = [_Provider("p1"), _Provider("p2")]
    first_error = ProviderRateLimitError(
        "retry hint cannot fit",
        provider_name="p1",
        status_code=429,
        error_code="RESOURCE_EXHAUSTED",
    )
    executor = _Executor([first_error, "fallback-ok"], max_retries=1)
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([100.0, 100.2, 100.4]),
    )

    result = await _chat_handler(
        providers,
        executor,
        timeout=10.0,
    ).execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "fallback-ok"
    assert executor.retries_seen == [0, 0]
    assert executor.budgets[0] is executor.budgets[1]
    assert executor.budgets[1].retries_used == 0


@pytest.mark.asyncio
async def test_r10_d_fallback_exhaustion_preserves_last_provider_cause_and_detail(
    monkeypatch,
):
    providers = [_Provider("p1"), _Provider("p2")]
    first = ProviderError(
        "first failed",
        provider_name="p1",
        status_code=503,
        error_code="UPSTREAM_UNAVAILABLE",
    )
    last = ProviderRateLimitError(
        "second failed",
        provider_name="p2",
        status_code=429,
        error_code="RESOURCE_EXHAUSTED",
    )
    executor = _Executor([first, last], max_retries=2)
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([100.0, 100.1, 100.2, 100.3]),
    )

    with pytest.raises(NoAvailableProviderError) as raised:
        await _chat_handler(
            providers,
            executor,
            timeout=10.0,
        ).execute_with_fallback(
            object(),
            {"model": "logical-model"},
        )

    error = raised.value
    assert error.code == PROVIDER_FALLBACK_EXHAUSTED
    assert error.provider_name == "p2"
    assert error.status_code == 429
    assert error.error_code == "RESOURCE_EXHAUSTED"
    assert error.__cause__ is last


@pytest.mark.asyncio
async def test_r10_d_deadline_exhaustion_is_not_rewritten_as_fallback_exhaustion(
    monkeypatch,
):
    providers = [_Provider("p1"), _Provider("p2")]
    first = ProviderError("p1 failed", provider_name="p1")
    executor = _Executor([first, "should-not-run"], max_retries=2)
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([100.0, 100.5, 110.0]),
    )

    with pytest.raises(ProviderDeadlineExceededError) as raised:
        await _chat_handler(
            providers,
            executor,
            timeout=10.0,
        ).execute_with_fallback(
            object(),
            {"model": "logical-model"},
        )

    assert raised.value.code == PROVIDER_DEADLINE_EXCEEDED
    assert executor.provider_calls == ["p1"]
    assert providers[1].probe_timeouts == []


@pytest.mark.asyncio
async def test_r10_d_embedding_reuses_same_budget_across_fallback(
    monkeypatch,
):
    providers = [_Provider("p1"), _Provider("p2")]

    def consume_then_fail(call_budget):
        assert call_budget.try_consume_retry() is True
        raise ProviderError("embedding p1 failed", provider_name="p1")

    executor = _Executor([consume_then_fail, "embedding-ok"], max_retries=2)
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([50.0, 50.25, 50.5]),
    )

    result = await _embedding_handler(
        providers,
        executor,
        timeout=5.0,
    ).execute(
        object(),
        {"model": "embedding-model", "input": ["x"]},
    )

    assert result == "embedding-ok"
    assert executor.provider_calls == ["p1", "p2"]
    assert executor.budgets[0] is executor.budgets[1]
    assert executor.retries_seen == [0, 1]
    assert executor.budgets[1].retries_used == 1
    assert providers[0].probe_timeouts == [4.75]
    assert providers[1].probe_timeouts == [4.5]


@pytest.mark.asyncio
async def test_r10_d_embedding_deadline_remains_deadline_error(
    monkeypatch,
):
    providers = [_Provider("p1"), _Provider("p2")]
    executor = _Executor(
        [ProviderError("p1 failed", provider_name="p1"), "never"],
        max_retries=1,
    )
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([10.0, 10.5, 15.0]),
    )

    with pytest.raises(ProviderDeadlineExceededError) as raised:
        await _embedding_handler(
            providers,
            executor,
            timeout=5.0,
        ).execute(
            object(),
            {"model": "embedding-model", "input": ["x"]},
        )

    assert raised.value.code == PROVIDER_DEADLINE_EXCEEDED
    assert executor.provider_calls == ["p1"]


class _RetryingExecutor:
    def __init__(self):
        self.retry_policy = RetryPolicy(max_retries=1)
        self.budgets = []
        self.retries_seen = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, call_budget, **kwargs):
        self.budgets.append(call_budget)
        self.retries_seen.append(call_budget.retries_used)

        async def attempt():
            outcome = provider.attempt_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        return await self.retry_policy.apply(
            attempt,
            provider.name,
            call_budget=call_budget,
        )


@pytest.mark.asyncio
async def test_r10_d_retry_delay_that_cannot_fit_falls_back_immediately(
    monkeypatch,
):
    first = _Provider("p1")
    second = _Provider("p2")
    first.attempt_outcomes = [
        ProviderRateLimitError(
            "retry later",
            provider_name="p1",
            status_code=429,
            error_code="RESOURCE_EXHAUSTED",
            retry_hint=ProviderRetryHint(
                retry_after_seconds=20.0,
                source=ProviderRetryHintSource.RETRY_AFTER,
            ),
        )
    ]
    second.attempt_outcomes = ["fallback-ok"]

    executor = _RetryingExecutor()
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        lambda: 100.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: 100.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    result = await _chat_handler(
        [first, second],
        executor,
        timeout=10.0,
    ).execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "fallback-ok"
    assert sleeps == []
    assert len(executor.budgets) == 2
    assert executor.budgets[0] is executor.budgets[1]
    assert executor.retries_seen == [0, 0]
    assert executor.budgets[1].retries_used == 0


class _ProbeErrorProvider(_Provider):
    def __init__(self, name, probe_error):
        super().__init__(name)
        self.probe_error = probe_error

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.probe_timeouts.append(timeout)
        raise self.probe_error


@pytest.mark.asyncio
async def test_r10_d_chat_raw_probe_failure_keeps_last_provider_and_raw_cause(
    monkeypatch,
):
    request = httpx.Request(
        "GET",
        "https://provider.example/models",
    )
    raw_probe_error = httpx.ReadError(
        "probe read failed",
        request=request,
    )
    providers = [
        _Provider("p1"),
        _ProbeErrorProvider("p2", raw_probe_error),
    ]
    executor = _Executor(
        [ProviderError("p1 failed", provider_name="p1")],
        max_retries=1,
    )
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([100.0, 100.1, 100.2, 100.3]),
    )

    with pytest.raises(NoAvailableProviderError) as raised:
        await _chat_handler(
            providers,
            executor,
            timeout=10.0,
        ).execute_with_fallback(
            object(),
            {"model": "logical-model"},
        )

    error = raised.value
    assert error.code == PROVIDER_FALLBACK_EXHAUSTED
    assert error.provider_name == "p2"
    assert error.status_code is None
    assert error.__cause__ is raw_probe_error
    assert executor.provider_calls == ["p1"]


@pytest.mark.asyncio
async def test_r10_d_embedding_raw_http_probe_failure_keeps_structured_detail(
    monkeypatch,
):
    request = httpx.Request(
        "GET",
        "https://provider.example/models",
    )
    response = httpx.Response(
        429,
        request=request,
        json={
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": "probe quota",
            }
        },
    )
    raw_probe_error = httpx.HTTPStatusError(
        "probe 429",
        request=request,
        response=response,
    )
    providers = [
        _Provider("p1"),
        _ProbeErrorProvider("p2", raw_probe_error),
    ]
    executor = _Executor(
        [ProviderError("p1 failed", provider_name="p1")],
        max_retries=1,
    )
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([50.0, 50.1, 50.2, 50.3]),
    )

    with pytest.raises(NoAvailableProviderError) as raised:
        await _embedding_handler(
            providers,
            executor,
            timeout=5.0,
        ).execute(
            object(),
            {"model": "embedding-model", "input": ["x"]},
        )

    error = raised.value
    assert error.code == PROVIDER_FALLBACK_EXHAUSTED
    assert error.provider_name == "p2"
    assert error.status_code == 429
    assert error.error_code == "RESOURCE_EXHAUSTED"
    assert error.__cause__ is raw_probe_error
    assert executor.provider_calls == ["p1"]


@pytest.mark.asyncio
async def test_r10_d_chat_providerless_probe_error_uses_current_provider_identity(
    monkeypatch,
):
    providerless_error = ProviderError("model probe failed")
    providers = [
        _Provider("p1"),
        _ProbeErrorProvider("ollama", providerless_error),
    ]
    executor = _Executor(
        [ProviderError("p1 failed", provider_name="p1")],
        max_retries=1,
    )
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([100.0, 100.1, 100.2, 100.3]),
    )

    with pytest.raises(NoAvailableProviderError) as raised:
        await _chat_handler(
            providers,
            executor,
            timeout=10.0,
        ).execute_with_fallback(
            object(),
            {"model": "logical-model"},
        )

    error = raised.value
    assert error.code == PROVIDER_FALLBACK_EXHAUSTED
    assert error.provider_name == "ollama"
    assert error.__cause__ is providerless_error


@pytest.mark.asyncio
async def test_r10_d_embedding_providerless_probe_error_uses_current_provider_identity(
    monkeypatch,
):
    providerless_error = ProviderError("model probe failed")
    providers = [
        _Provider("p1"),
        _ProbeErrorProvider("ollama", providerless_error),
    ]
    executor = _Executor(
        [ProviderError("p1 failed", provider_name="p1")],
        max_retries=1,
    )
    monkeypatch.setattr(
        "se.src.provider.handlers.base.monotonic",
        _Clock([50.0, 50.1, 50.2, 50.3]),
    )

    with pytest.raises(NoAvailableProviderError) as raised:
        await _embedding_handler(
            providers,
            executor,
            timeout=5.0,
        ).execute(
            object(),
            {"model": "embedding-model", "input": ["x"]},
        )

    error = raised.value
    assert error.code == PROVIDER_FALLBACK_EXHAUSTED
    assert error.provider_name == "ollama"
    assert error.__cause__ is providerless_error
