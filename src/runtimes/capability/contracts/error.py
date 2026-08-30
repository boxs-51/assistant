import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CapabilityError(Exception):
    """Stable machine-readable failure contract and runtime exception."""

    code: str
    message: str
    category: str = "INTERNAL"
    retryable: bool = False
    safe_for_client: bool = False
    cause_type: Optional[str] = None
    capability_id: Optional[str] = None
    invocation_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def model_dump(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "retryable": self.retryable,
            "safe_for_client": self.safe_for_client,
            "cause_type": self.cause_type,
            "capability_id": self.capability_id,
            "invocation_id": self.invocation_id,
            "details": dict(self.details),
        }

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        capability_id: str | None = None,
        invocation_id: str | None = None,
    ) -> "CapabilityError":
        if isinstance(exc, PermissionError):
            category = "AUTHORIZATION"
            code = "CAPABILITY_UNAUTHORIZED"
            safe = True
        elif isinstance(exc, ValueError):
            category = "VALIDATION"
            code = "CAPABILITY_INVALID_ARGUMENT"
            safe = True
        elif isinstance(exc, asyncio.CancelledError):
            category = "CANCELLED"
            code = "CAPABILITY_CANCELLED"
            safe = True
        else:
            category = "INTERNAL"
            code = "CAPABILITY_EXECUTION_FAILED"
            safe = False
        return cls(
            code=code,
            message=str(exc),
            category=category,
            retryable=False,
            safe_for_client=safe,
            cause_type=type(exc).__name__,
            capability_id=capability_id,
            invocation_id=invocation_id,
        )