from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from ...capability.contracts.definition import (
    CapabilityExecutionMode,
    CapabilityIdempotency,
    CapabilityKind,
)
from ...capability.contracts.invocation import (
    CapabilityInvocationState,
    RemoteOutcomeState,
)
from .inference import InferenceMessage
from .resume import ResumeInvocationActionKind, ResumeTriggerType


class RecoveryInferenceDisposition(str, Enum):
    """Read-only inference identity disposition frozen by R12-F1."""

    NO_INFERENCE = "NO_INFERENCE"
    RESUME_SAME_LOGICAL_INFERENCE = "RESUME_SAME_LOGICAL_INFERENCE"
    NEW_LOGICAL_INFERENCE_ALLOWED = "NEW_LOGICAL_INFERENCE_ALLOWED"


@dataclass(frozen=True, slots=True)
class RecoveryContinuationAuthority:
    """Exact client-affine continuation target proven during planning."""

    target_client_id: str
    target_connection_id: str
    implementation_id: str


@dataclass(frozen=True, slots=True)
class RecoveryToolQuotaAuthority:
    """Existing UBQ-3 authority observed without mutating renewable quota."""

    reservation_id: str
    idempotency_key: str | None
    window_epoch: int
    reservation_state: str
    historical_bridge: bool


@dataclass(frozen=True, slots=True)
class RecoveryInvocationAction:
    """One immutable R6-backed action in a SERVER_RECOVERY plan."""

    invocation_id: str
    tool_call_id: str
    ordinal: int
    capability_id: str
    capability_version: str
    request_fingerprint: str
    kind: CapabilityKind
    execution_mode: CapabilityExecutionMode
    idempotency: CapabilityIdempotency
    expected_invocation_revision: int
    expected_invocation_state: CapabilityInvocationState
    expected_remote_outcome_state: RemoteOutcomeState | None
    origin_client_id: str | None
    origin_connection_id: str | None
    action: ResumeInvocationActionKind
    continuation_authority: RecoveryContinuationAuthority | None = None
    tool_quota_authority: RecoveryToolQuotaAuthority | None = None


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    """Immutable R12-F1 read-only SERVER_RECOVERY planning evidence."""

    execution_id: str
    checkpoint_id: str
    expected_execution_revision: int
    recovery_fingerprint: str
    expected_unowned_lease_generation: int
    checkpoint_origin_client_id: str | None
    checkpoint_origin_connection_id: str | None

    agent_id: str
    session_id: str
    task_id: str | None
    task_revision: int | None
    branch_id: str | None
    branch_revision: int | None
    parent_execution_id: str | None
    retry_of_execution_id: str | None
    base_execution_id: str | None
    base_checkpoint_id: str | None
    resolved_recovery_principal: str

    iteration: int
    recovery_iteration_id: str | None
    inference_request_id: str | None
    inference_disposition: RecoveryInferenceDisposition
    ordered_tool_call_ids: tuple[str, ...]
    transcript_snapshot: tuple[InferenceMessage, ...]

    remaining_active_budget_seconds: float
    wait_expires_at: datetime | None

    task_budget_incarnation_generation: int | None
    task_budget_revision: int | None

    target_trigger: ResumeTriggerType
    target_client_id: str | None
    target_connection_id: str | None
    invocation_actions: tuple[RecoveryInvocationAction, ...]

    plan_fingerprint: str


@dataclass(frozen=True, slots=True)
class RecoveryActivationSpec:
    """One bounded R12-F2 atomic SERVER_RECOVERY activation attempt."""

    plan: RecoveryPlan
    claim_id: str
    resume_request_id: str
    expected_claim_revision: int
    activation_owner_instance_id: str
    activation_now_utc: datetime
    activation_lease_expires_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveryActivationResult:
    """Durable R12-F2 activation authority returned after one atomic commit."""

    claim_id: str
    resume_request_id: str
    execution_id: str
    checkpoint_id: str
    source_execution_revision: int
    consumed_execution_revision: int
    activation_owner_instance_id: str
    lease_generation: int
    lease_expires_at: datetime
    already_consumed: bool = False


def recovery_plan_fingerprint(
    values: RecoveryPlan | Mapping[str, Any],
) -> str:
    """Hash every immutable R12-F1 authority field except the fingerprint itself."""

    def get(name: str):
        return values[name] if isinstance(values, Mapping) else getattr(values, name)

    actions = []
    for item in get("invocation_actions"):
        continuation = item.continuation_authority
        quota = item.tool_quota_authority
        actions.append(
            {
                "invocation_id": item.invocation_id,
                "tool_call_id": item.tool_call_id,
                "ordinal": item.ordinal,
                "capability_id": item.capability_id,
                "capability_version": item.capability_version,
                "request_fingerprint": item.request_fingerprint,
                "kind": item.kind.value,
                "execution_mode": item.execution_mode.value,
                "idempotency": item.idempotency.value,
                "expected_invocation_revision": item.expected_invocation_revision,
                "expected_invocation_state": item.expected_invocation_state.value,
                "expected_remote_outcome_state": (
                    item.expected_remote_outcome_state.value
                    if item.expected_remote_outcome_state is not None
                    else None
                ),
                "origin_client_id": item.origin_client_id,
                "origin_connection_id": item.origin_connection_id,
                "action": item.action.value,
                "continuation_authority": (
                    {
                        "target_client_id": continuation.target_client_id,
                        "target_connection_id": continuation.target_connection_id,
                        "implementation_id": continuation.implementation_id,
                    }
                    if continuation is not None
                    else None
                ),
                "tool_quota_authority": (
                    {
                        "reservation_id": quota.reservation_id,
                        "idempotency_key": quota.idempotency_key,
                        "window_epoch": quota.window_epoch,
                        "reservation_state": quota.reservation_state,
                        "historical_bridge": quota.historical_bridge,
                    }
                    if quota is not None
                    else None
                ),
            }
        )

    wait_expires_at = get("wait_expires_at")
    payload = {
        "execution_id": get("execution_id"),
        "checkpoint_id": get("checkpoint_id"),
        "expected_execution_revision": get("expected_execution_revision"),
        "recovery_fingerprint": get("recovery_fingerprint"),
        "expected_unowned_lease_generation": get(
            "expected_unowned_lease_generation"
        ),
        "checkpoint_origin_client_id": get("checkpoint_origin_client_id"),
        "checkpoint_origin_connection_id": get("checkpoint_origin_connection_id"),
        "agent_id": get("agent_id"),
        "session_id": get("session_id"),
        "task_id": get("task_id"),
        "branch_id": get("branch_id"),
        "parent_execution_id": get("parent_execution_id"),
        "retry_of_execution_id": get("retry_of_execution_id"),
        "base_execution_id": get("base_execution_id"),
        "base_checkpoint_id": get("base_checkpoint_id"),
        "resolved_recovery_principal": get("resolved_recovery_principal"),
        "iteration": get("iteration"),
        "recovery_iteration_id": get("recovery_iteration_id"),
        "inference_request_id": get("inference_request_id"),
        "inference_disposition": get("inference_disposition").value,
        "ordered_tool_call_ids": list(get("ordered_tool_call_ids")),
        "transcript_snapshot": [
            item.model_dump(mode="json")
            for item in get("transcript_snapshot")
        ],
        "remaining_active_budget_seconds": get(
            "remaining_active_budget_seconds"
        ),
        "wait_expires_at": (
            wait_expires_at.isoformat()
            if wait_expires_at is not None
            else None
        ),
        "task_budget_incarnation_generation": get(
            "task_budget_incarnation_generation"
        ),
        "target_trigger": get("target_trigger").value,
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


__all__ = [
    "RecoveryActivationResult",
    "RecoveryActivationSpec",
    "RecoveryContinuationAuthority",
    "RecoveryInferenceDisposition",
    "RecoveryInvocationAction",
    "RecoveryPlan",
    "RecoveryToolQuotaAuthority",
    "recovery_plan_fingerprint",
]