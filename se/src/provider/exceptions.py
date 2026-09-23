from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

import httpx

if TYPE_CHECKING:
    from .retry_contracts import ProviderRetryHint


PROVIDER_ERROR = "PROVIDER_ERROR"
PROVIDER_AUTHENTICATION_FAILED = "PROVIDER_AUTHENTICATION_FAILED"
PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
PROVIDER_MODEL_UNAVAILABLE = "PROVIDER_MODEL_UNAVAILABLE"
PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
PROVIDER_FALLBACK_EXHAUSTED = "PROVIDER_FALLBACK_EXHAUSTED"
PROVIDER_DEADLINE_EXCEEDED = "PROVIDER_DEADLINE_EXCEEDED"


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
        retry_hint: Optional["ProviderRetryHint"] = None,
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


def wrap_provider_exception(error: Exception, provider_name: str) -> ProviderError:
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
                        error_code = error_data.get("code")
                elif "detail" in raw_response:
                    message = raw_response["detail"]
        except Exception:
            message = error.response.text[:500]

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
