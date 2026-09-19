from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class ContinuationState(str, Enum):
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    # Deprecated source alias; canonical serialization is WAITING.
    WAITING_FOR_CONNECTION = "WAITING"
    READY_TO_MERGE = "READY_TO_MERGE"


class CheckpointReason(str, Enum):
    ITERATION = "ITERATION"
    CONNECTION_DISCONNECTED = "CONNECTION_DISCONNECTED"
    WAITING_FOR_CONNECTION = "WAITING_FOR_CONNECTION"
    RECONNECTED = "RECONNECTED"
    READY_TO_MERGE = "READY_TO_MERGE"


@dataclass(frozen=True, slots=True)
class ExecutionCheckpoint:
    checkpoint_id: str
    execution_id: str
    session_id: str
    reason: CheckpointReason
    state: ContinuationState
    wait_reason: str | None = None
    parent_checkpoint_id: str | None = None
    origin_connection_id: str | None = None
    current_connection_id: str | None = None
    pending_invocation_id: str | None = None
    pending_tool_call_id: str | None = None
    pending_capability_id: str | None = None
    iteration: int = 0
    transcript: tuple[dict[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class ContinuationBranch:
    branch_id: str
    execution_id: str
    base_checkpoint_id: str
    connection_id: str
    owner_user_id: str
    state: ContinuationState = ContinuationState.READY_TO_MERGE
    metadata: Mapping[str, Any] = field(default_factory=dict)


__all__ = [
    "CheckpointReason",
    "ContinuationBranch",
    "ContinuationState",
    "ExecutionCheckpoint",
]
