from __future__ import annotations

import re
from typing import Final

GENERIC_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "INVALID_ARGUMENT",
        "DEPENDENCY_UNAVAILABLE",
        "NOT_FOUND",
        "AMBIGUOUS_TARGET",
        "TIMEOUT",
        "OUTPUT_LIMIT_EXCEEDED",
        "PERMISSION_DENIED",
        "OPERATION_FAILED",
        "INTERNAL_ERROR",
    }
)

_ERROR_CODE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ToolFoundationError(ValueError):
    """Base exception for malformed Tools V1 foundation contracts."""


class ToolContractError(ToolFoundationError):
    """Raised when a ToolResult or shared contract is malformed."""


class ToolMetadataError(ToolFoundationError):
    """Raised when Metadata V2 is structurally or semantically invalid."""


class ToolLimitConfigError(ToolFoundationError):
    """Raised when a hard-limit specification or explicit value is invalid."""


class ToolJsonSafetyError(ToolFoundationError):
    """Raised when a value cannot be represented by the frozen JSON contract."""


def validate_error_code(code: str) -> str:
    """Return *code* when it is a stable machine-readable error identifier."""
    if not isinstance(code, str) or not _ERROR_CODE_RE.fullmatch(code):
        raise ToolContractError(
            "error code must match ^[A-Z][A-Z0-9_]*$"
        )
    return code
