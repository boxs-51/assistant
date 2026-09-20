"""Canonical capability execution contracts."""

from .definition import (
    CapabilityDefinition,
    CapabilityIdempotency,
)
from .context import CapabilityExecutionContext
from .result import CapabilityResult
from .error import (
    CapabilityError,
    REMOTE_INVOCATION_CONFLICT,
    REMOTE_OUTCOME_UNKNOWN,
    REMOTE_RESULT_RECONCILIATION_REQUIRED,
    R6_RECONCILIATION_ERROR_CODES,
)
from .invocation import RemoteOutcomeState
from .reconciliation import (
    RemoteReconciliationResult,
    RemoteReconciliationStatus,
    RemoteTerminalType,
)

__all__ = [
    "CapabilityDefinition",
    "CapabilityIdempotency",
    "CapabilityExecutionContext",
    "CapabilityResult",
    "CapabilityError",
    "RemoteOutcomeState",
    "REMOTE_INVOCATION_CONFLICT",
    "REMOTE_OUTCOME_UNKNOWN",
    "REMOTE_RESULT_RECONCILIATION_REQUIRED",
    "R6_RECONCILIATION_ERROR_CODES",
    "RemoteReconciliationResult",
    "RemoteReconciliationStatus",
    "RemoteTerminalType",
]