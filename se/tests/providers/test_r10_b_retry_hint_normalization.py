from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from se.src.provider.exceptions import (
    PROVIDER_RATE_LIMITED,
    ProviderRateLimitError,
    ProviderUnavailableError,
    extract_rate_limit_retry_hint,
    parse_google_rpc_retry_info_hint,
    parse_retry_after_hint,
    wrap_provider_exception,
)
from se.src.provider.policies.retry import RetryPolicy
from se.src.provider.retry_contracts import ProviderRetryHintSource


def _status_error(
    status_code: int,
    *,
    headers: dict[str, str] | None = None,
    payload=None,
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example/v1/chat")
    response = httpx.Response(
        status_code,
        headers=headers,
        json=payload,
        request=request,
    )
    return httpx.HTTPStatusError(
        f"provider returned {status_code}",
        request=request,
        response=response,
    )


def test_r10_b_retry_after_delta_seconds_normalizes():
    hint = parse_retry_after_hint("12")

    assert hint is not None
    assert hint.retry_after_seconds == 12.0
    assert hint.source is ProviderRetryHintSource.RETRY_AFTER


def test_r10_b_retry_after_http_date_uses_wall_clock_only_for_relative_delay():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    retry_at = now + timedelta(seconds=30)

    hint = parse_retry_after_hint(
        retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        now_utc=now,
    )

    assert hint is not None
    assert hint.retry_after_seconds == 30.0
    assert hint.source is ProviderRetryHintSource.RETRY_AFTER


def test_r10_b_retry_after_past_http_date_clamps_to_zero():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    retry_at = now - timedelta(seconds=5)

    hint = parse_retry_after_hint(
        retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        now_utc=now,
    )

    assert hint is not None
    assert hint.retry_after_seconds == 0.0


@pytest.mark.parametrize(
    "value",
    ["", " ", "-1", "1.5", "nan", "inf", "not-a-date"],
)
def test_r10_b_invalid_retry_after_is_ignored(value):
    assert parse_retry_after_hint(value) is None


def test_r10_b_google_rpc_retry_info_normalizes_fractional_duration():
    payload = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "2.500s",
                }
            ],
        }
    }

    hint = parse_google_rpc_retry_info_hint(payload)

    assert hint is not None
    assert hint.retry_after_seconds == 2.5
    assert hint.source is ProviderRetryHintSource.GOOGLE_RPC_RETRY_INFO


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"error": {}},
        {"error": {"details": "not-a-list"}},
        {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "-1s",
                    }
                ]
            }
        },
        {
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "nans",
                    }
                ]
            }
        },
    ],
)
def test_r10_b_missing_or_malformed_google_retry_info_is_ignored(payload):
    assert parse_google_rpc_retry_info_hint(payload) is None


def test_r10_b_429_prefers_strongest_valid_provider_hint():
    payload = {
        "error": {
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "5s",
                }
            ],
        }
    }
    error = _status_error(
        429,
        headers={"Retry-After": "2"},
        payload=payload,
    )

    normalized = wrap_provider_exception(error, "gemini")

    assert isinstance(normalized, ProviderRateLimitError)
    assert normalized.code == PROVIDER_RATE_LIMITED
    assert normalized.error_code == "RESOURCE_EXHAUSTED"
    assert normalized.retry_after_seconds == 5.0
    assert (
        normalized.retry_hint.source
        is ProviderRetryHintSource.GOOGLE_RPC_RETRY_INFO
    )


def test_r10_b_non_429_does_not_acquire_retry_hint():
    payload = {
        "error": {
            "status": "UNAVAILABLE",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "10s",
                }
            ],
        }
    }
    raw = _status_error(
        503,
        headers={"Retry-After": "10"},
        payload=payload,
    )

    normalized = wrap_provider_exception(raw, "gemini")

    assert isinstance(normalized, ProviderUnavailableError)
    assert normalized.retry_hint is None
    assert extract_rate_limit_retry_hint(
        raw.response,
        payload,
    ) is None


def test_r10_b_structured_provider_error_normalization_is_idempotent():
    original = ProviderRateLimitError(
        "quota",
        provider_name="gemini",
        error_code="RESOURCE_EXHAUSTED",
    )

    assert wrap_provider_exception(original, "ignored-provider") is original


@pytest.mark.asyncio
async def test_r10_b_retry_policy_normalizes_hint_before_retry_decision(
    monkeypatch,
):
    raw = _status_error(
        429,
        headers={"Retry-After": "3"},
        payload={
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": "slow down",
            }
        },
    )
    calls = 0
    seen_errors = []
    sleeps = []

    async def execute():
        nonlocal calls
        calls += 1
        raise raw

    async def fake_sleep(delay):
        sleeps.append(delay)

    policy = RetryPolicy(max_retries=1)
    from se.src.provider.policies import retry as retry_module

    original_wrap = retry_module.wrap_provider_exception

    def inspect_normalization(error, provider_name):
        normalized = original_wrap(error, provider_name)
        seen_errors.append(normalized)
        return normalized

    monkeypatch.setattr(
        retry_module,
        "wrap_provider_exception",
        inspect_normalization,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )
    monkeypatch.setattr(
        "se.src.provider.policies.retry.random.uniform",
        lambda _a, _b: 0.0,
    )

    with pytest.raises(ProviderRateLimitError) as raised:
        await policy.apply(execute, "gemini")

    assert calls == 2
    assert sleeps == [1.0]
    assert len(seen_errors) == 2
    assert all(isinstance(error, ProviderRateLimitError) for error in seen_errors)
    assert seen_errors[0].retry_after_seconds == 3.0
    assert seen_errors[0].error_code == "RESOURCE_EXHAUSTED"
    assert raised.value.retry_after_seconds == 3.0
    assert raised.value.code == PROVIDER_RATE_LIMITED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_error",
    [
        _status_error(
            408,
            payload={"error": {"message": "request timeout"}},
        ),
        httpx.ReadError(
            "read failed",
            request=httpx.Request(
                "POST",
                "https://provider.example/v1/chat",
            ),
        ),
    ],
)
async def test_r10_b_normalization_does_not_broaden_legacy_retry_set(
    monkeypatch,
    raw_error,
):
    calls = 0
    sleeps = []

    async def execute():
        nonlocal calls
        calls += 1
        raise raw_error

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(
        "se.src.provider.policies.retry.asyncio.sleep",
        fake_sleep,
    )
    policy = RetryPolicy(max_retries=2)

    with pytest.raises(ProviderUnavailableError):
        await policy.apply(execute, "test-provider")

    assert calls == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_r10_b_non_provider_exception_identity_is_preserved():
    original = ValueError("provider adapter bug")

    async def execute():
        raise original

    policy = RetryPolicy(max_retries=2)

    with pytest.raises(ValueError) as raised:
        await policy.apply(execute, "test-provider")

    assert raised.value is original
