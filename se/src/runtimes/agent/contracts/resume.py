from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class ToolResultCommitState(str, Enum):
    """Durable certainty of the Agent tool-result projection."""

    PROVISIONAL = "PROVISIONAL"
    COMMITTED = "COMMITTED"


class ResumeClaimState(str, Enum):
    """Durable lifecycle of one resume authority attempt."""

    CREATED = "CREATED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class DurableExecutionCheckpoint:
    """Immutable normalized R7 safe-point representation.

    This coexists with the Phase 6.9 ``ExecutionCheckpoint`` during the
    R7 migration.  R7-H removes the legacy branch/merge representation after
    all canonical writers/readers have moved to this normalized contract.
    """

    checkpoint_id: str
    execution_id: str
    execution_revision: int
    session_id: str
    iteration: int
    wait_reason: str
    task_id: str | None = None
    branch_id: str | None = None
    parent_checkpoint_id: str | None = None
    remaining_active_budget_seconds: float | None = None
    wait_expires_at: datetime | None = None
    origin_client_id: str | None = None
    origin_connection_id: str | None = None
    transcript_snapshot: tuple[dict[str, Any], ...] | None = None
    transcript_ref: str | None = None
    transcript_version: int | None = None
    side_effect_watermark: str | None = None
    legacy_source_key: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass(frozen=True, slots=True)
class CheckpointPendingInvocation:
    """One ordered R6 invocation referenced by an R7 checkpoint."""

    checkpoint_id: str
    ordinal: int
    invocation_id: str
    invocation_revision: int
    tool_call_id: str
    capability_id: str


@dataclass(frozen=True, slots=True)
class ResumeClaim:
    """Durable resume intent; CREATED owns no execution authority."""

    claim_id: str
    execution_id: str
    checkpoint_id: str
    resume_request_id: str
    expected_execution_revision: int
    user_id: str
    wait_reason: str
    trigger_type: str
    plan_fingerprint: str
    claim_expires_at: datetime
    client_id: str | None = None
    connection_id: str | None = None
    state: ResumeClaimState = ResumeClaimState.CREATED
    revision: int = 0
    rejection_code: str | None = None
    consumed_execution_revision: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    consumed_at: datetime | None = None
    rejected_at: datetime | None = None
    expired_at: datetime | None = None


__all__ = [
    "CheckpointPendingInvocation",
    "DurableExecutionCheckpoint",
    "ResumeClaim",
    "ResumeClaimState",
    "ToolResultCommitState",
]
