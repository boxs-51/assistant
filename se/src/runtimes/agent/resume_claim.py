from __future__ import annotations


class ResumeClaimError(RuntimeError):
    """Stable R7-F domain error emitted before R7-G wire mapping."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class ResumeClaimRejected(ResumeClaimError):
    """The durable resume request cannot acquire authority as submitted."""


class ResumeClaimDeferred(ResumeClaimError):
    """No authority was acquired; retry may become eligible later."""


__all__ = [
    "ResumeClaimDeferred",
    "ResumeClaimError",
    "ResumeClaimRejected",
]
