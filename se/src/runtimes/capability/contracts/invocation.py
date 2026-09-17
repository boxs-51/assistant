from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .definition import CapabilityExecutionMode, CapabilityKind


class CapabilityInvocationState(str, Enum):
    CREATED = "CREATED"
    DISPATCHING = "DISPATCHING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class CapabilityWaitReason(str, Enum):
    CONNECTION = "CONNECTION"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    DEPENDENCY = "DEPENDENCY"
    RETRY_BACKOFF = "RETRY_BACKOFF"
    RESOURCE = "RESOURCE"


TERMINAL_INVOCATION_STATES = frozenset(
    {
        CapabilityInvocationState.COMPLETED,
        CapabilityInvocationState.FAILED,
        CapabilityInvocationState.CANCELLED,
        CapabilityInvocationState.TIMED_OUT,
    }
)


class CapabilityInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str
    capability_id: str
    kind: CapabilityKind
    execution_mode: CapabilityExecutionMode
    implementation_id: str | None = None
    driver_kind: str | None = None
    state: CapabilityInvocationState = CapabilityInvocationState.CREATED
    wait_reason: CapabilityWaitReason | None = None
    session_id: str | None = None
    turn_id: str | None = None
    execution_id: str | None = None
    workflow_id: str | None = None
    tool_call_id: str | None = None
    connection_id: str | None = None
    attempt: int = 0
    max_attempts: int = 1
    arguments: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    error: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    deadline_at: datetime | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    revision: int = 0


class CapabilityInvocationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    invocation_id: str
    attempt_number: int
    implementation_id: str
    driver_kind: str
    connection_id: str | None = None
    state: CapabilityInvocationState = CapabilityInvocationState.DISPATCHING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityInvocationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    event_name: str
    invocation_id: str
    attempt_id: str | None = None
    capability_id: str
    implementation_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    execution_id: str | None = None
    previous_state: CapabilityInvocationState | None = None
    state: CapabilityInvocationState
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None
    causation_id: str | None = None
    trace_id: str | None = None

