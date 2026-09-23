from __future__ import annotations

import asyncio

import pytest

from se.src.provider.exceptions import (
    PROVIDER_DEADLINE_EXCEEDED,
    ProviderDeadlineExceededError,
    ProviderRateLimitError,
)
from se.src.provider.executor import ProviderExecutor
from se.src.provider.policies.retry import RetryPolicy
from se.src.provider.retry_contracts import (
    ProviderCallBudget,
    ProviderRetryHint,
    ProviderRetryHintSource,
)


def _rate_limit_error(
    *,
    retry_after_seconds: float | None = None,
) -> ProviderRateLimitError:
    hint = None
    if retry_after_seconds is not None:
        hint = ProviderRetryHint(
            retry_after_seconds=retry_after_seconds,
            source=ProviderRetryHintSource.RETRY_AFTER,
        )
    return ProviderRateLimitError(
        "rate limited",
        provider_name="provider-a",
        status_code=429,
        error_code="RESOURCE_EXHAUSTED",
        retry_hint=hint,
    )


@pytest.mark.asyncio
async def test_r10_c_hint_greater_than_remaining_escapes_without_sleep_or_retry(
    monkeypatch,
):
    budget = ProviderCallBudget(
        deadline_monotonic=102.0,
        max_retries=1,
    )
    error = _rate_limit_error(retry_after_seconds=3.0)
    calls = 0
    sleeps = []

    async def execute():
        nonlocal calls
        calls += 1
        raise error

    async def fake_sleep(delay):
        sleeps.append(delay)

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

    policy = RetryPolicy(max_retries=99)
    with pytest.raises(ProviderRateLimitError) as raised:
        await policy.apply(
            execute,
            "provider-a",
            call_budget=budget,
        )

    assert raised.value is error
    assert calls == 1
    assert sleeps == []
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_r10_c_hint_within_remaining_consumes_one_token_after_backoff(
    monkeypatch,
):
    clock = {"now": 100.0}
    budget = ProviderCallBudget(
        deadline_monotonic=110.0,
        max_retries=1,
    )
    error = _rate_limit_error(retry_after_seconds=2.0)
    calls = 0
    sleeps = []

    async def execute():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return "ok"

    async def fake_sleep(delay):
        sleeps.append(delay)
        assert budget.retries_used == 0
        clock["now"] += delay

    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    result = await RetryPolicy(max_retries=99).apply(
        execute,
        "provider-a",
        call_budget=budget,
    )

    assert result == "ok"
    assert calls == 2
    assert sleeps == [2.0]
    assert budget.retries_used == 1
    assert budget.retries_remaining == 0


@pytest.mark.asyncio
async def test_r10_c_shared_budget_does_not_reset_when_already_consumed(
    monkeypatch,
):
    clock = {"now": 100.0}
    budget = ProviderCallBudget(
        deadline_monotonic=120.0,
        max_retries=2,
    )
    assert budget.try_consume_retry() is True

    calls = 0
    sleeps = []
    error = _rate_limit_error()

    async def execute():
        nonlocal calls
        calls += 1
        raise error

    async def fake_sleep(delay):
        sleeps.append(delay)
        clock["now"] += delay

    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    with pytest.raises(ProviderRateLimitError) as raised:
        await RetryPolicy(max_retries=99).apply(
            execute,
            "provider-a",
            call_budget=budget,
        )

    assert raised.value is error
    assert calls == 2
    assert sleeps == [1.0]
    assert budget.retries_used == 2
    assert budget.retries_remaining == 0


@pytest.mark.asyncio
async def test_r10_c_exhausted_shared_budget_never_sleeps_or_retries(
    monkeypatch,
):
    budget = ProviderCallBudget(
        deadline_monotonic=120.0,
        max_retries=1,
    )
    assert budget.try_consume_retry() is True
    calls = 0
    sleeps = []
    error = _rate_limit_error()

    async def execute():
        nonlocal calls
        calls += 1
        raise error

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: 100.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    with pytest.raises(ProviderRateLimitError):
        await RetryPolicy(max_retries=99).apply(
            execute,
            "provider-a",
            call_budget=budget,
        )

    assert calls == 1
    assert sleeps == []
    assert budget.retries_used == 1


@pytest.mark.asyncio
async def test_r10_c_cancellation_during_backoff_consumes_no_retry_token(
    monkeypatch,
):
    budget = ProviderCallBudget(
        deadline_monotonic=120.0,
        max_retries=1,
    )
    calls = 0
    error = _rate_limit_error()

    async def execute():
        nonlocal calls
        calls += 1
        raise error

    async def cancelled_sleep(_delay):
        raise asyncio.CancelledError()

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
        cancelled_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await RetryPolicy(max_retries=99).apply(
            execute,
            "provider-a",
            call_budget=budget,
        )

    assert calls == 1
    assert budget.retries_used == 0
    assert budget.retries_remaining == 1


@pytest.mark.asyncio
async def test_r10_c_deadline_expiring_during_backoff_does_not_consume_token(
    monkeypatch,
):
    clock = {"now": 100.0}
    budget = ProviderCallBudget(
        deadline_monotonic=105.0,
        max_retries=1,
    )
    calls = 0
    error = _rate_limit_error()

    async def execute():
        nonlocal calls
        calls += 1
        raise error

    async def fake_sleep(_delay):
        clock["now"] = 105.0

    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    with pytest.raises(ProviderDeadlineExceededError) as raised:
        await RetryPolicy(max_retries=99).apply(
            execute,
            "provider-a",
            call_budget=budget,
        )

    assert raised.value.code == PROVIDER_DEADLINE_EXCEEDED
    assert calls == 1
    assert budget.retries_used == 0


@pytest.mark.asyncio
async def test_r10_c_no_budget_path_preserves_legacy_local_delay(
    monkeypatch,
):
    error = _rate_limit_error(retry_after_seconds=50.0)
    calls = 0
    sleeps = []

    async def execute():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return "ok"

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    result = await RetryPolicy(max_retries=1).apply(
        execute,
        "provider-a",
    )

    assert result == "ok"
    assert calls == 2
    assert sleeps == [1.0]


class _Breaker:
    def __init__(self):
        self.before_calls = 0
        self.success_calls = 0
        self.failure_calls = 0

    async def before_request(self):
        self.before_calls += 1

    async def on_success(self):
        self.success_calls += 1

    async def on_failure(self):
        self.failure_calls += 1

    async def is_open(self):
        return False


class _BreakerManager:
    def __init__(self, breaker):
        self.breaker = breaker

    async def get_breaker(self, _provider_name):
        return self.breaker


class _Chat:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.timeouts = []
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        self.timeouts.append(kwargs.get("timeout"))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _Provider:
    def __init__(self, outcomes):
        self.name = "provider-a"
        self.chat = _Chat(outcomes)


@pytest.mark.asyncio
async def test_r10_c_executor_passes_decreasing_timeout_within_budget(
    monkeypatch,
):
    clock = {"now": 100.0}
    budget = ProviderCallBudget(
        deadline_monotonic=105.0,
        max_retries=1,
    )
    error = _rate_limit_error()
    provider = _Provider([error, "ok"])
    breaker = _Breaker()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=99),
    )

    async def fake_sleep(delay):
        clock["now"] += delay

    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.executor.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    result = await executor.execute(
        provider=provider,
        http_client=object(),
        body={"model": "m"},
        timeout=50.0,
        call_budget=budget,
    )

    assert result == "ok"
    assert provider.chat.calls == 2
    assert provider.chat.timeouts == [5.0, 4.0]
    assert all(0 < timeout <= 5.0 for timeout in provider.chat.timeouts)
    assert budget.retries_used == 1
    assert breaker.before_calls == 1
    assert breaker.success_calls == 1
    assert breaker.failure_calls == 0


@pytest.mark.asyncio
async def test_r10_c_expired_budget_before_first_attempt_does_not_penalize_breaker(
    monkeypatch,
):
    budget = ProviderCallBudget(
        deadline_monotonic=100.0,
        max_retries=1,
    )
    provider = _Provider(["should-not-run"])
    breaker = _Breaker()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=99),
    )

    monkeypatch.setattr(
        "se.src.provider.executor.time.monotonic",
        lambda: 100.0,
    )

    with pytest.raises(ProviderDeadlineExceededError) as raised:
        await executor.execute(
            provider=provider,
            http_client=object(),
            body={"model": "m"},
            call_budget=budget,
        )

    assert raised.value.code == PROVIDER_DEADLINE_EXCEEDED
    assert provider.chat.calls == 0
    assert breaker.before_calls == 0
    assert breaker.success_calls == 0
    assert breaker.failure_calls == 0


@pytest.mark.asyncio
async def test_r10_c_execute_generic_uses_same_shared_retry_budget(
    monkeypatch,
):
    clock = {"now": 100.0}
    budget = ProviderCallBudget(
        deadline_monotonic=110.0,
        max_retries=1,
    )
    breaker = _Breaker()
    provider = _Provider([])
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=99),
    )
    calls = 0
    error = _rate_limit_error()

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return "generic-ok"

    async def fake_sleep(delay):
        clock["now"] += delay

    monkeypatch.setattr(
        "se.src.provider.policies.retry.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.executor.time.monotonic",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )

    result = await executor.execute_generic(
        provider,
        operation,
        call_budget=budget,
    )

    assert result == "generic-ok"
    assert calls == 2
    assert budget.retries_used == 1
    assert breaker.before_calls == 1
    assert breaker.success_calls == 1
    assert breaker.failure_calls == 0


@pytest.mark.asyncio
async def test_r10_c_execute_generic_expired_budget_skips_operation_and_breaker(
    monkeypatch,
):
    budget = ProviderCallBudget(
        deadline_monotonic=100.0,
        max_retries=1,
    )
    breaker = _Breaker()
    provider = _Provider([])
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=RetryPolicy(max_retries=99),
    )
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return "unexpected"

    monkeypatch.setattr(
        "se.src.provider.executor.time.monotonic",
        lambda: 100.0,
    )

    with pytest.raises(ProviderDeadlineExceededError):
        await executor.execute_generic(
            provider,
            operation,
            call_budget=budget,
        )

    assert calls == 0
    assert breaker.before_calls == 0
    assert breaker.failure_calls == 0


class _LegacyRetryPolicy:
    def __init__(self):
        self.calls = 0

    async def apply(self, execution_func, provider_name):
        self.calls += 1
        assert provider_name == "provider-a"
        return await execution_func()


@pytest.mark.asyncio
async def test_r10_c_no_budget_executor_preserves_legacy_retry_policy_interface():
    provider = _Provider(["ok"])
    breaker = _Breaker()
    retry_policy = _LegacyRetryPolicy()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=retry_policy,
    )

    result = await executor.execute(
        provider=provider,
        http_client=object(),
        body={"model": "m"},
    )

    assert result == "ok"
    assert retry_policy.calls == 1
    assert provider.chat.calls == 1
    assert breaker.success_calls == 1
    assert breaker.failure_calls == 0


@pytest.mark.asyncio
async def test_r10_c_no_budget_generic_preserves_legacy_retry_policy_interface():
    provider = _Provider([])
    breaker = _Breaker()
    retry_policy = _LegacyRetryPolicy()
    executor = ProviderExecutor(
        _BreakerManager(breaker),
        retry_policy=retry_policy,
    )
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        return "generic-ok"

    result = await executor.execute_generic(
        provider,
        operation,
    )

    assert result == "generic-ok"
    assert retry_policy.calls == 1
    assert calls == 1
    assert breaker.success_calls == 1
    assert breaker.failure_calls == 0
