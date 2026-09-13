from __future__ import annotations

from typing import Any

from ...capability.contracts.error import CapabilityError

CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
CAPABILITY_UNAUTHORIZED = "CAPABILITY_UNAUTHORIZED"
CAPABILITY_INVALID_ARGUMENT = "CAPABILITY_INVALID_ARGUMENT"
CAPABILITY_SCHEMA_INVALID = "CAPABILITY_SCHEMA_INVALID"
CAPABILITY_TIMEOUT = "CAPABILITY_TIMEOUT"
CAPABILITY_CANCELLED = "CAPABILITY_CANCELLED"
CAPABILITY_EXECUTION_FAILED = "CAPABILITY_EXECUTION_FAILED"

AGENT_TOOL_POLICY_DENIED = "AGENT_TOOL_POLICY_DENIED"
AGENT_TOOL_NOT_VISIBLE = "AGENT_TOOL_NOT_VISIBLE"
AGENT_TOOL_BUDGET_EXCEEDED = "AGENT_TOOL_BUDGET_EXCEEDED"

CANONICAL_TOOL_ERROR_CODES = frozenset(
    {
        CAPABILITY_NOT_FOUND,
        CAPABILITY_UNAUTHORIZED,
        CAPABILITY_INVALID_ARGUMENT,
        CAPABILITY_TIMEOUT,
        CAPABILITY_CANCELLED,
        CAPABILITY_EXECUTION_FAILED,
        AGENT_TOOL_POLICY_DENIED,
        AGENT_TOOL_NOT_VISIBLE,
        AGENT_TOOL_BUDGET_EXCEEDED,
    }
)

INTERNAL_TOOL_ERROR_CODES = frozenset({CAPABILITY_SCHEMA_INVALID})


class ToolArgumentParseError(ValueError):
    """Provider tool-argument parsing failure with a stable Agent error code."""

    code = CAPABILITY_INVALID_ARGUMENT


def normalize_tool_exception(
    exc: BaseException,
    *,
    capability_id: str,
    invocation_id: str,
) -> CapabilityError:
    """Normalize downstream failures without misclassifying driver ValueError.

    ``CapabilityError`` instances already carry the canonical retry/error
    semantics established by CapabilityRuntime and are preserved verbatim.
    Unknown exceptions become ``CAPABILITY_EXECUTION_FAILED`` while retaining
    an optional original application error code in ``details`` and preserving
    the existing ``retryable`` signal used by the Phase 5.6 coordinator.
    """
    if isinstance(exc, CapabilityError):
        return exc

    original_code = getattr(exc, "code", None)
    details: dict[str, Any] = {}
    if original_code:
        details["original_error_code"] = str(original_code)

    return CapabilityError(
        code=CAPABILITY_EXECUTION_FAILED,
        message=str(exc),
        category="EXECUTION",
        retryable=bool(getattr(exc, "retryable", False)),
        safe_for_client=False,
        cause_type=type(exc).__name__,
        capability_id=capability_id,
        invocation_id=invocation_id,
        details=details,
    )
