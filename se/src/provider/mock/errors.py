from __future__ import annotations
from typing import Any

from ..exceptions import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)


def build_mock_error(*, provider_name: str, error_type: str, message: str,
                     status_code: int | None = None, error_code: str | None = None,
                     raw_response: Any = None) -> ProviderError:
    kind = error_type.strip().lower()
    if kind in {"auth", "unauthorized", "forbidden"}:
        code = 403 if kind == "forbidden" else 401
        return ProviderAuthenticationError(
            message=message, provider_name=provider_name,
            status_code=status_code or code,
            error_code=error_code or "mock_auth_error", raw_response=raw_response,
        )
    if kind in {"rate_limit", "ratelimit", "429"}:
        return ProviderRateLimitError(
            message=message, provider_name=provider_name,
            status_code=status_code or 429,
            error_code=error_code or "mock_rate_limit", raw_response=raw_response,
        )
    if kind in {"timeout", "unavailable", "service_unavailable", "503"}:
        return ProviderUnavailableError(
            message=message, provider_name=provider_name,
            status_code=status_code or 503,
            error_code=error_code or "mock_unavailable", raw_response=raw_response,
            is_network_error=(kind == "timeout"),
        )
    return ProviderError(
        message=message, provider_name=provider_name,
        status_code=status_code, error_code=error_code or "mock_error",
        raw_response=raw_response,
    )
