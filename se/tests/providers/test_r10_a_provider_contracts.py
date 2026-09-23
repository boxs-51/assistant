from __future__ import annotations

import math

import pytest

from se.src.provider.exceptions import (
    PROVIDER_AUTHENTICATION_FAILED,
    PROVIDER_DEADLINE_EXCEEDED,
    PROVIDER_ERROR,
    PROVIDER_FALLBACK_EXHAUSTED,
    PROVIDER_MODEL_UNAVAILABLE,
    PROVIDER_RATE_LIMITED,
    PROVIDER_RESPONSE_INVALID,
    PROVIDER_UNAVAILABLE,
    NoAvailableProviderError,
    ProviderAuthenticationError,
    ProviderDeadlineExceededError,
    ProviderError,
    ProviderModelUnavailableError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    ResponseValidationError,
)
from se.src.provider.retry_contracts import (
    ProviderCallBudget,
    ProviderRetryHint,
    ProviderRetryHintSource,
)


def test_r10_a_provider_system_error_codes_are_stable_and_distinct_from_native_code():
    error = ProviderRateLimitError(
        "quota",
        provider_name="gemini",
        status_code=429,
        error_code="RESOURCE_EXHAUSTED",
    )

    assert error.code == PROVIDER_RATE_LIMITED
    assert error.error_code == "RESOURCE_EXHAUSTED"
    assert error.failure_domain == "PROVIDER"
    assert error.retryable is True

    assert ProviderError.code == PROVIDER_ERROR
    assert ProviderAuthenticationError.code == PROVIDER_AUTHENTICATION_FAILED
    assert ProviderUnavailableError.code == PROVIDER_UNAVAILABLE
    assert ProviderModelUnavailableError.code == PROVIDER_MODEL_UNAVAILABLE
    assert ResponseValidationError.code == PROVIDER_RESPONSE_INVALID
    assert NoAvailableProviderError.code == PROVIDER_FALLBACK_EXHAUSTED
    assert ProviderDeadlineExceededError.code == PROVIDER_DEADLINE_EXCEEDED


def test_r10_a_provider_error_retryability_remains_class_owned():
    assert ProviderError.retryable is False
    assert ProviderAuthenticationError.retryable is False
    assert ProviderRateLimitError.retryable is True
    assert ProviderUnavailableError.retryable is True
    assert ProviderModelUnavailableError.retryable is False
    assert ResponseValidationError.retryable is False
    assert NoAvailableProviderError.retryable is False
    assert ProviderDeadlineExceededError.retryable is False


def test_r10_a_retry_hint_is_normalized_and_exposed_by_provider_error():
    hint = ProviderRetryHint(
        retry_after_seconds=2,
        source=ProviderRetryHintSource.RETRY_AFTER,
    )
    error = ProviderRateLimitError(
        "slow down",
        provider_name="openai",
        retry_hint=hint,
    )

    assert hint.retry_after_seconds == 2.0
    assert hint.source is ProviderRetryHintSource.RETRY_AFTER
    assert error.retry_hint is hint
    assert error.retry_after_seconds == 2.0


@pytest.mark.parametrize(
    "value",
    [-1, float("inf"), float("-inf"), float("nan")],
)
def test_r10_a_retry_hint_rejects_negative_or_non_finite_delay(value):
    with pytest.raises(ValueError):
        ProviderRetryHint(
            retry_after_seconds=value,
            source=ProviderRetryHintSource.RETRY_AFTER,
        )


def test_r10_a_retry_hint_rejects_unknown_source_and_boolean_delay():
    with pytest.raises(ValueError):
        ProviderRetryHint(
            retry_after_seconds=1,
            source="UNKNOWN",
        )

    with pytest.raises(TypeError):
        ProviderRetryHint(
            retry_after_seconds=True,
            source=ProviderRetryHintSource.RETRY_AFTER,
        )


def test_r10_a_provider_call_budget_uses_one_monotonic_deadline():
    budget = ProviderCallBudget.from_timeout(
        now_monotonic=100.0,
        timeout_seconds=5.0,
        max_retries=2,
    )

    assert budget.deadline_monotonic == 105.0
    assert budget.remaining_seconds(now_monotonic=100.0) == 5.0
    assert budget.remaining_seconds(now_monotonic=103.5) == 1.5
    assert budget.remaining_seconds(now_monotonic=106.0) == 0.0
    assert budget.retries_used == 0
    assert budget.retries_remaining == 2


def test_r10_a_provider_call_budget_retry_tokens_are_shared_and_bounded():
    budget = ProviderCallBudget(
        deadline_monotonic=110.0,
        max_retries=2,
    )
    first_provider_view = budget
    fallback_provider_view = budget

    assert first_provider_view.try_consume_retry() is True
    assert fallback_provider_view.retries_used == 1
    assert fallback_provider_view.retries_remaining == 1

    assert fallback_provider_view.try_consume_retry() is True
    assert first_provider_view.retries_used == 2
    assert first_provider_view.retries_remaining == 0

    assert first_provider_view.try_consume_retry() is False
    assert fallback_provider_view.retries_used == 2


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"deadline_monotonic": math.inf, "max_retries": 1}, ValueError),
        ({"deadline_monotonic": 10.0, "max_retries": -1}, ValueError),
        ({"deadline_monotonic": 10.0, "max_retries": 1, "retries_used": 2}, ValueError),
        ({"deadline_monotonic": 10.0, "max_retries": 1.5}, TypeError),
        ({"deadline_monotonic": True, "max_retries": 1}, TypeError),
    ],
)
def test_r10_a_provider_call_budget_rejects_invalid_state(kwargs, error_type):
    with pytest.raises(error_type):
        ProviderCallBudget(**kwargs)


@pytest.mark.parametrize(
    ("timeout", "error_type"),
    [
        (0.0, ValueError),
        (-1.0, ValueError),
        (math.inf, ValueError),
        (True, TypeError),
    ],
)
def test_r10_a_provider_call_budget_rejects_invalid_timeout(timeout, error_type):
    with pytest.raises(error_type):
        ProviderCallBudget.from_timeout(
            now_monotonic=100.0,
            timeout_seconds=timeout,
            max_retries=1,
        )
