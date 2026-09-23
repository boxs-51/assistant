from __future__ import annotations

from typing import Any, Optional


class WebToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def dependency_error(name: str, exc: BaseException | None = None) -> WebToolError:
    details: dict[str, Any] = {"dependency": name}
    if exc is not None:
        details["exception_type"] = type(exc).__name__
    return WebToolError(
        "DEPENDENCY_UNAVAILABLE",
        f"{name} is unavailable in the current environment",
        details=details,
    )
