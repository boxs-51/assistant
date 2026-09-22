from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from .inference import InferenceMessage


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _get(values: Any, name: str) -> Any:
    return values[name] if isinstance(values, Mapping) else getattr(values, name)


@dataclass(frozen=True, slots=True)
class ForkSideEffectSnapshot:
    invocation_id: str
    invocation_revision: int
    capability_id: str
    capability_version: str | None
    request_fingerprint: str | None
    idempotency: str
    state: str
    remote_outcome_state: str | None
    tool_call_id: str
    committed_result_fingerprint: str


@dataclass(frozen=True, slots=True)
class ForkAdmission:
    """Committed R8-D FORK result returned by atomic consume."""

    task_id: str
    fork_request_id: str
    plan_fingerprint: str

    branch_id: str
    branch_revision: int

    execution_id: str
    execution_revision: int

    task_revision: int
    task_budget_revision: int


@dataclass(frozen=True, slots=True)
class ForkPlan:
    """Immutable R8-C read-only proof for one future R8-D FORK consume."""

    fork_request_id: str
    plan_fingerprint: str

    task_id: str
    expected_task_revision: int
    session_id: str

    source_branch_id: str
    expected_branch_revision: int

    source_execution_id: str
    expected_execution_revision: int
    source_agent_id: str
    correlation_id: str

    source_checkpoint_id: str
    checkpoint_iteration: int

    expected_task_budget_revision: int
    budget_policy_fingerprint: str

    base_transcript: tuple[InferenceMessage, ...]
    base_transcript_fingerprint: str

    side_effects: tuple[ForkSideEffectSnapshot, ...]
    side_effect_fingerprint: str

    overlay_messages: tuple[dict[str, Any], ...]
    target_user_id: str


def committed_result_fingerprint(values: Mapping[str, Any] | Any) -> str:
    payload = {
        "execution_id": _get(values, "execution_id"),
        "invocation_id": _get(values, "invocation_id"),
        "tool_call_id": _get(values, "tool_call_id"),
        "capability_id": _get(values, "capability_id"),
        "success": bool(_get(values, "success")),
        "output": _get(values, "output"),
        "error_code": _get(values, "error_code"),
        "error_message": _get(values, "error_message"),
        "retryable": bool(_get(values, "retryable")),
        "attempt": int(_get(values, "attempt")),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def fork_transcript_fingerprint(
    transcript: tuple[InferenceMessage, ...] | list[InferenceMessage],
) -> str:
    payload = [
        item.model_dump(mode="json")
        if isinstance(item, InferenceMessage)
        else InferenceMessage.model_validate(item).model_dump(mode="json")
        for item in transcript
    ]
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def fork_side_effect_fingerprint(
    side_effects: tuple[ForkSideEffectSnapshot, ...]
    | list[ForkSideEffectSnapshot],
) -> str:
    payload = [
        {
            "invocation_id": _get(item, "invocation_id"),
            "invocation_revision": int(_get(item, "invocation_revision")),
            "capability_id": _get(item, "capability_id"),
            "capability_version": _get(item, "capability_version"),
            "request_fingerprint": _get(item, "request_fingerprint"),
            "idempotency": _get(item, "idempotency"),
            "state": _get(item, "state"),
            "remote_outcome_state": _get(item, "remote_outcome_state"),
            "tool_call_id": _get(item, "tool_call_id"),
            "committed_result_fingerprint": _get(
                item,
                "committed_result_fingerprint",
            ),
        }
        for item in side_effects
    ]
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def fork_plan_fingerprint(values: ForkPlan | Mapping[str, Any]) -> str:
    """Hash immutable FORK semantics, excluding request/created identities."""

    transcript = tuple(_get(values, "base_transcript"))
    side_effects = tuple(_get(values, "side_effects"))
    payload = {
        "task_id": _get(values, "task_id"),
        "expected_task_revision": int(_get(values, "expected_task_revision")),
        "session_id": _get(values, "session_id"),
        "source_branch_id": _get(values, "source_branch_id"),
        "expected_branch_revision": int(
            _get(values, "expected_branch_revision")
        ),
        "source_execution_id": _get(values, "source_execution_id"),
        "expected_execution_revision": int(
            _get(values, "expected_execution_revision")
        ),
        "source_agent_id": _get(values, "source_agent_id"),
        "correlation_id": _get(values, "correlation_id"),
        "source_checkpoint_id": _get(values, "source_checkpoint_id"),
        "checkpoint_iteration": int(_get(values, "checkpoint_iteration")),
        "expected_task_budget_revision": int(
            _get(values, "expected_task_budget_revision")
        ),
        "budget_policy_fingerprint": _get(
            values,
            "budget_policy_fingerprint",
        ),
        "base_transcript": [
            item.model_dump(mode="json")
            if isinstance(item, InferenceMessage)
            else InferenceMessage.model_validate(item).model_dump(mode="json")
            for item in transcript
        ],
        "base_transcript_fingerprint": _get(
            values,
            "base_transcript_fingerprint",
        ),
        "side_effects": [
            {
                "invocation_id": _get(item, "invocation_id"),
                "invocation_revision": int(
                    _get(item, "invocation_revision")
                ),
                "capability_id": _get(item, "capability_id"),
                "capability_version": _get(item, "capability_version"),
                "request_fingerprint": _get(item, "request_fingerprint"),
                "idempotency": _get(item, "idempotency"),
                "state": _get(item, "state"),
                "remote_outcome_state": _get(
                    item,
                    "remote_outcome_state",
                ),
                "tool_call_id": _get(item, "tool_call_id"),
                "committed_result_fingerprint": _get(
                    item,
                    "committed_result_fingerprint",
                ),
            }
            for item in side_effects
        ],
        "side_effect_fingerprint": _get(
            values,
            "side_effect_fingerprint",
        ),
        "overlay_messages": [
            dict(item) for item in _get(values, "overlay_messages")
        ],
        "target_user_id": _get(values, "target_user_id"),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


__all__ = [
    "ForkAdmission",
    "ForkPlan",
    "ForkSideEffectSnapshot",
    "committed_result_fingerprint",
    "fork_plan_fingerprint",
    "fork_side_effect_fingerprint",
    "fork_transcript_fingerprint",
]
