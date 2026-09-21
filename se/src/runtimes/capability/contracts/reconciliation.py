from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class RemoteReconciliationStatus(str, Enum):
    RUNNING = "RUNNING"
    TERMINAL = "TERMINAL"
    UNKNOWN = "UNKNOWN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"


class RemoteTerminalType(str, Enum):
    RESULT = "result"
    ERROR = "error"
    CANCELLED = "cancelled"


class RemoteReconciliationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: str
    status: RemoteReconciliationStatus
    capability_id: str
    capability_version: str
    request_fingerprint: str
    terminal_type: RemoteTerminalType | None = None
    terminal_payload: dict[str, Any] | None = None
    ledger_state: Literal["PREPARED", "RUNNING", "TERMINAL"] | None = None
