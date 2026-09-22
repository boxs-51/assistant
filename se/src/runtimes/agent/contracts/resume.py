from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from ...capability.contracts.definition import CapabilityIdempotency
from ...capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)
from .inference import InferenceMessage


class ToolResultCommitState(str, Enum):
    """Durable certainty of the Agent tool-result projection."""

    PROVISIONAL = "PROVISIONAL"
    COMMITTED = "COMMITTED"


class ResumeInvocationActionKind(str, Enum):
    """Only execution-safe dispositions R7 may carry past planning."""

    REUSE_COMMITTED = "REUSE_COMMITTED"
    DISPATCH_NOT_DISPATCHED = "DISPATCH_NOT_DISPATCHED"
    REPLAY_SAFE = "REPLAY_SAFE"


class ResumeClaimState(str, Enum):
    """Durable lifecycle of one resume authority attempt."""

    CREATED = "CREATED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ResumeTriggerType(str, Enum):
    """Canonical durable trigger vocabulary for R7 resume intent."""

    CLIENT_RECONNECT = "CLIENT_RECONNECT"
    SERVER_RECOVERY = "SERVER_RECOVERY"
    MANUAL = "MANUAL"
    DEPENDENCY_READY = "DEPENDENCY_READY"
    RESOURCE_READY = "RESOURCE_READY"


def normalize_resume_trigger_type(value: "ResumeTriggerType | str") -> ResumeTriggerType:
    """Normalize the pre-R7 connection trigger spelling without minting new values."""

    raw = value.value if isinstance(value, ResumeTriggerType) else str(value)
    if raw == "CONNECTION_RECONNECT":
        raw = ResumeTriggerType.CLIENT_RECONNECT.value
    return ResumeTriggerType(raw)



@dataclass(frozen=True, slots=True)
class DurableExecutionCheckpoint:
    """Immutable normalized R7 safe-point representation.

    This coexists with the Phase 6.9 ``ExecutionCheckpoint`` during the
    R7 migration.  R7-I owns removal of the legacy branch/merge representation
    after R7-H client resume orchestration is proven on the normalized path.
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
    capability_version: str | None = None
    request_fingerprint: str | None = None
    idempotency: str = "UNKNOWN"
    observed_remote_outcome_state: str | None = None
    origin_client_id: str | None = None
    origin_connection_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResumeInvocationAction:
    """One immutable R6-backed disposition for a checkpointed invocation."""

    invocation_id: str
    tool_call_id: str
    ordinal: int
    capability_id: str
    capability_version: str
    request_fingerprint: str
    idempotency: CapabilityIdempotency
    expected_invocation_revision: int
    expected_invocation_state: CapabilityInvocationState
    expected_remote_outcome_state: RemoteOutcomeState | None
    action: ResumeInvocationActionKind


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """Immutable, read-only R7-D plan. It owns no execution authority."""

    execution_id: str
    checkpoint_id: str
    expected_execution_revision: int
    plan_fingerprint: str

    agent_id: str
    session_id: str
    task_id: str | None
    branch_id: str | None
    parent_execution_id: str | None
    retry_of_execution_id: str | None
    base_execution_id: str | None
    base_checkpoint_id: str | None

    correlation_id: str
    trace_id: str | None
    request_id: str | None

    iteration: int
    ordered_tool_call_ids: tuple[str, ...]
    transcript_snapshot: tuple[InferenceMessage, ...]

    remaining_active_budget_seconds: float
    wait_expires_at: datetime | None

    target_user_id: str
    target_client_id: str | None
    target_connection_id: str | None

    invocation_actions: tuple[ResumeInvocationAction, ...]


def resume_plan_fingerprint(values: "ResumePlan | Mapping[str, Any]") -> str:
    """Hash only immutable ResumePlan semantics.

    The helper is shared by R7-D planning and R7-F claim consumption so the
    claim transaction never trusts a caller-supplied plan_fingerprint alone.
    """

    def get(name: str):
        return values[name] if isinstance(values, Mapping) else getattr(values, name)

    actions = [
        {
            "invocation_id": item.invocation_id,
            "tool_call_id": item.tool_call_id,
            "ordinal": item.ordinal,
            "capability_id": item.capability_id,
            "capability_version": item.capability_version,
            "request_fingerprint": item.request_fingerprint,
            "idempotency": item.idempotency.value,
            "expected_invocation_revision": item.expected_invocation_revision,
            "expected_invocation_state": item.expected_invocation_state.value,
            "expected_remote_outcome_state": (
                item.expected_remote_outcome_state.value
                if item.expected_remote_outcome_state is not None
                else None
            ),
            "action": item.action.value,
        }
        for item in get("invocation_actions")
    ]
    wait_expires_at = get("wait_expires_at")
    payload = {
        "execution_id": get("execution_id"),
        "checkpoint_id": get("checkpoint_id"),
        "expected_execution_revision": get("expected_execution_revision"),
        "agent_id": get("agent_id"),
        "session_id": get("session_id"),
        "task_id": get("task_id"),
        "branch_id": get("branch_id"),
        "parent_execution_id": get("parent_execution_id"),
        "retry_of_execution_id": get("retry_of_execution_id"),
        "base_execution_id": get("base_execution_id"),
        "base_checkpoint_id": get("base_checkpoint_id"),
        "correlation_id": get("correlation_id"),
        "trace_id": get("trace_id"),
        "request_id": get("request_id"),
        "iteration": get("iteration"),
        "ordered_tool_call_ids": list(get("ordered_tool_call_ids")),
        "transcript_snapshot": [
            item.model_dump(mode="json")
            for item in get("transcript_snapshot")
        ],
        "remaining_active_budget_seconds": get("remaining_active_budget_seconds"),
        "wait_expires_at": (
            wait_expires_at.isoformat()
            if wait_expires_at is not None
            else None
        ),
        "target_user_id": get("target_user_id"),
        "target_client_id": get("target_client_id"),
        "target_connection_id": get("target_connection_id"),
        "invocation_actions": actions,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ResumeClaimIntent:
    """Idempotent durable intent. Creation alone owns no execution authority."""

    resume_request_id: str
    execution_id: str
    checkpoint_id: str
    expected_execution_revision: int
    plan_fingerprint: str
    user_id: str
    wait_reason: str
    trigger_type: ResumeTriggerType
    claim_expires_at: datetime
    client_id: str | None = None
    connection_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class ResumeClaimConsumeSpec:
    """Frozen R7-F claim-consumption request."""

    plan: ResumePlan
    claim_id: str
    resume_request_id: str
    expected_claim_revision: int
    now_utc: datetime


@dataclass(frozen=True, slots=True)
class ResumeClaimConsumeResult:
    """Durable authority acquired by one consumed claim."""

    claim_id: str
    resume_request_id: str
    execution_id: str
    checkpoint_id: str
    source_execution_revision: int
    consumed_execution_revision: int
    remaining_active_budget_seconds: float
    bound_client_id: str | None
    bound_connection_id: str | None
    already_consumed: bool = False


__all__ = [
    "CheckpointPendingInvocation",
    "DurableExecutionCheckpoint",
    "ResumeInvocationAction",
    "ResumeInvocationActionKind",
    "ResumePlan",
    "ResumeClaim",
    "ResumeClaimConsumeResult",
    "ResumeClaimConsumeSpec",
    "ResumeClaimIntent",
    "ResumeClaimState",
    "ResumeTriggerType",
    "ToolResultCommitState",
    "normalize_resume_trigger_type",
    "resume_plan_fingerprint",
]
