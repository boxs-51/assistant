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


class ResumeActivationError(ResumeClaimError):
    """A CONSUMED claim could not be activated safely.

    R7-F4 surfaces this failure but deliberately does not recover, reject, or
    ACK it. R7-G owns post-claim recovery and wire semantics.
    """


__all__ = [
    "ResumeActivationError",
    "ResumeClaimDeferred",
    "ResumeClaimError",
    "ResumeClaimRejected",
]
