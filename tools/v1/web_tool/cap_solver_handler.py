from __future__ import annotations

from .errors import WebToolError


class CapSolverHandler:
    """Compatibility tombstone for the removed ordinary-read CAPTCHA path."""

    def __init__(self, api_key: str) -> None:
        del api_key
        raise WebToolError(
            "INVALID_ARGUMENT",
            "automatic CAPTCHA solving is not part of T6 ordinary web read",
        )
