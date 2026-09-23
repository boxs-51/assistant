from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import httpx

from .retry_contracts import ProviderRetryHint, ProviderRetryHintSource


PROVIDER_ERROR = "PROVIDER_ERROR"
PROVIDER_AUTHENTICATION_FAILED = "PROVIDER_AUTHENTICATION_FAILED"
PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
PROVIDER_MODEL_UNAVAILABLE = "PROVIDER_MODEL_UNAVAILABLE"
PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
PROVIDER_FALLBACK_EXHAUSTED = "PROVIDER_FALLBACK_EXHAUSTED"
PROVIDER_DEADLINE_EXCEEDED = "PROVIDER_DEADLINE_EXCEEDED"

_GOOGLE_RETRY_INFO_TYPE = "type.googleapis.com/google.rpc.RetryInfo"
_PROTO_DURATION_SECONDS_RE = re.compile(
    r"^(?P<seconds>\d+)(?:\.(?P<fraction>\d{1,9}))?s$"
)


class ProviderError(Exception):
    """Base exception for provider failures."""

    code = PROVIDER_ERROR
    failure_domain = "PROVIDER"
    retryable = False

    def __init__(
        self,
        message: str,
        provider_name: Optional[str] = None,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        raw_response: Optional[Any] = None,
        is_network_error: bool = False,
        retry_hint: Optional[ProviderRetryHint] = None,
    ):
        self.provider_name = provider_name
        self.status_code = status_code
        self.error_code = error_code
        self.raw_response = raw_response
        self.is_network_error = is_network_error
        self.retry_hint = retry_hint

        prefix = f"[{provider_name}]" if provider_name else ""
        if is_network_error:
            code_info = " (Network/Connection Error)"
        else:
            code_info = (
                f" (Status: {status_code}, Code: {error_code})"
                if status_code or error_code
                else ""
            )
        super().__init__(f"{prefix} {message}{code_info}")

    @property
    def retry_after_seconds(self) -> Optional[float]:
        if self.retry_hint is None:
            return None
        return self.retry_hint.retry_after_seconds


class ProviderFallbackExhaustedError(ProviderError):
    """All eligible providers for one logical call have been exhausted."""

    code = PROVIDER_FALLBACK_EXHAUSTED


class NoAvailableProviderError(ProviderFallbackExhaustedError):
    """Compatibility name for an exhausted/unavailable provider chain."""


class ProviderAuthenticationError(ProviderError):
    """Authentication/authorization failure from a provider."""

    code = PROVIDER_AUTHENTICATION_FAILED


class ProviderRateLimitError(ProviderError):
    """Provider quota or rate-limit failure."""

    code = PROVIDER_RATE_LIMITED
    retryable = True


class ProviderUnavailableError(ProviderError):
    """Transient provider/network unavailability."""

    code = PROVIDER_UNAVAILABLE
    retryable = True


class ProviderModelUnavailableError(ProviderError):
    """Mapped model is definitively unavailable on this provider."""

    code = PROVIDER_MODEL_UNAVAILABLE


class ResponseValidationError(ProviderError):
    """Provider response is structurally invalid."""

    code = PROVIDER_RESPONSE_INVALID


class ProviderDeadlineExceededError(ProviderError):
    """Logical provider-call deadline is exhausted."""

    code = PROVIDER_DEADLINE_EXCEEDED


def parse_retry_after_hint(
    value: str | None,
    *,
    now_utc: datetime | None = None,
) -> ProviderRetryHint | None:
    """Parse HTTP Retry-After into a relative retry delay.

    Delta-seconds are accepted only in their RFC integer form. HTTP-date uses
    wall clock solely to derive a relative delay; later logical deadline
    accounting remains monotonic.
    """

    if value is None:
        return None
    value = value.strip()
    if not value:
        return None

    if value.isdigit():
        try:
            seconds = float(int(value, 10))
            return ProviderRetryHint(
                retry_after_seconds=seconds,
                source=ProviderRetryHintSource.RETRY_AFTER,
            )
        except (OverflowError, ValueError):
            return None

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at is None:
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)

    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        seconds = max(
            0.0,
            (retry_at.astimezone(timezone.utc) - now.astimezone(timezone.utc))
            .total_seconds(),
        )
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(seconds):
        return None
    return ProviderRetryHint(
        retry_after_seconds=seconds,
        source=ProviderRetryHintSource.RETRY_AFTER,
    )


def parse_google_rpc_retry_info_hint(
    raw_response: Any,
) -> ProviderRetryHint | None:
    """Parse Gemini/google.rpc RetryInfo retryDelay from an error payload."""

    if not isinstance(raw_response, dict):
        return None
    error_data = raw_response.get("error")
    if not isinstance(error_data, dict):
        return None
    details = error_data.get("details")
    if not isinstance(details, list):
        return None

    for detail in details:
        if not isinstance(detail, dict):
            continue
        if detail.get("@type") != _GOOGLE_RETRY_INFO_TYPE:
            continue
        retry_delay = detail.get("retryDelay")
        if not isinstance(retry_delay, str):
            continue
        match = _PROTO_DURATION_SECONDS_RE.fullmatch(retry_delay.strip())
        if match is None:
            continue

        seconds = float(match.group("seconds"))
        fraction = match.group("fraction")
        if fraction:
            seconds += int(fraction) / (10 ** len(fraction))
        if not math.isfinite(seconds):
            continue

        return ProviderRetryHint(
            retry_after_seconds=seconds,
            source=ProviderRetryHintSource.GOOGLE_RPC_RETRY_INFO,
        )
    return None


def extract_rate_limit_retry_hint(
    response: httpx.Response,
    raw_response: Any,
    *,
    now_utc: datetime | None = None,
) -> ProviderRetryHint | None:
    """Select the strongest valid hint attached to one HTTP 429 response."""

    if response.status_code != 429:
        return None

    hints = [
        parse_retry_after_hint(
            response.headers.get("Retry-After"),
            now_utc=now_utc,
        ),
        parse_google_rpc_retry_info_hint(raw_response),
    ]
    valid_hints = [hint for hint in hints if hint is not None]
    if not valid_hints:
        return None
    return max(valid_hints, key=lambda hint: hint.retry_after_seconds)


def wrap_provider_exception(
    error: Exception,
    provider_name: str,
    *,
    now_utc: datetime | None = None,
) -> ProviderError:
    """Normalize httpx/provider failures into structured provider errors."""

    if isinstance(error, ProviderError):
        return error

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        error_code = None
        message = str(error)
        raw_response = None

        try:
            raw_response = error.response.json()
            if isinstance(raw_response, dict):
                if "error" in raw_response:
                    error_data = raw_response["error"]
                    if isinstance(error_data, dict):
                        message = error_data.get("message", message)
                        error_code = (
                            error_data.get("status")
                            or error_data.get("code")
                        )
                elif "detail" in raw_response:
                    message = raw_response["detail"]
        except Exception:
            message = error.response.text[:500]

        retry_hint = extract_rate_limit_retry_hint(
            error.response,
            raw_response,
            now_utc=now_utc,
        )

        if status_code in (401, 403):
            return ProviderAuthenticationError(
                message=f"Auth Failed: {message}",
                provider_name=provider_name,
                status_code=status_code,
                error_code=error_code,
                raw_response=raw_response,
            )
        if status_code == 429:
            return ProviderRateLimitError(
                message=f"Quota/Rate Limit Exceeded: {message}",
                provider_name=provider_name,
                status_code=status_code,
                error_code=error_code,
                raw_response=raw_response,
                retry_hint=retry_hint,
            )
        if status_code == 408 or 500 <= status_code < 600:
            return ProviderUnavailableError(
                message=f"Provider Service Unavailable: {message}",
                provider_name=provider_name,
                status_code=status_code,
                error_code=error_code,
                raw_response=raw_response,
            )
        return ProviderError(
            message=message,
            provider_name=provider_name,
            status_code=status_code,
            error_code=error_code,
            raw_response=raw_response,
        )

    if isinstance(error, httpx.RequestError):
        prefix = (
            "Network Timeout"
            if isinstance(error, httpx.TimeoutException)
            else "Network Request Failed"
        )
        return ProviderUnavailableError(
            message=f"{prefix} (No Response): {str(error)}",
            provider_name=provider_name,
            is_network_error=True,
        )

    return ProviderError(message=str(error), provider_name=provider_name)
