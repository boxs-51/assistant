from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


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
class RetryPlan:
    """Immutable AE-R9-B read-only proof for one future RETRY admission."""

    retry_request_id: str
    plan_fingerprint: str

    task_id: str
    expected_task_revision: int
    session_id: str

    branch_id: str
    expected_branch_revision: int

    source_execution_id: str
    expected_execution_revision: int
    source_execution_state: str
    source_agent_id: str
    source_checkpoint_id: str | None

    expected_task_budget_revision: int
    budget_policy_fingerprint: str

    parent_execution_id: str | None
    base_execution_id: str | None
    base_checkpoint_id: str | None
    correlation_id: str

    request_fingerprint: str
    source_context_fingerprint: str
    fresh_active_budget_seconds: float

    target_user_id: str


@dataclass(frozen=True, slots=True)
class RetryAdmission:
    """Committed AE-R9-B RETRY result returned by atomic admission."""

    task_id: str
    retry_request_id: str
    plan_fingerprint: str

    branch_id: str
    branch_revision: int

    source_execution_id: str
    source_checkpoint_id: str | None

    execution_id: str
    execution_revision: int

    task_revision: int
    task_budget_revision: int


def retry_value_fingerprint(value: Any) -> str:
    """Fingerprint one JSON-compatible RETRY planning value."""

    return hashlib.sha256(_canonical_json(value)).hexdigest()


def retry_plan_fingerprint(values: RetryPlan | Mapping[str, Any]) -> str:
    """Hash immutable RETRY semantics, excluding request/created identities."""

    payload = {
        "task_id": _get(values, "task_id"),
        "expected_task_revision": int(_get(values, "expected_task_revision")),
        "session_id": _get(values, "session_id"),
        "branch_id": _get(values, "branch_id"),
        "expected_branch_revision": int(
            _get(values, "expected_branch_revision")
        ),
        "source_execution_id": _get(values, "source_execution_id"),
        "expected_execution_revision": int(
            _get(values, "expected_execution_revision")
        ),
        "source_execution_state": _get(values, "source_execution_state"),
        "source_agent_id": _get(values, "source_agent_id"),
        "source_checkpoint_id": _get(values, "source_checkpoint_id"),
        "expected_task_budget_revision": int(
            _get(values, "expected_task_budget_revision")
        ),
        "budget_policy_fingerprint": _get(
            values, "budget_policy_fingerprint"
        ),
        "parent_execution_id": _get(values, "parent_execution_id"),
        "base_execution_id": _get(values, "base_execution_id"),
        "base_checkpoint_id": _get(values, "base_checkpoint_id"),
        "correlation_id": _get(values, "correlation_id"),
        "request_fingerprint": _get(values, "request_fingerprint"),
        "source_context_fingerprint": _get(
            values, "source_context_fingerprint"
        ),
        "fresh_active_budget_seconds": float(
            _get(values, "fresh_active_budget_seconds")
        ),
        "target_user_id": _get(values, "target_user_id"),
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


__all__ = [
    "RetryAdmission",
    "RetryPlan",
    "retry_plan_fingerprint",
    "retry_value_fingerprint",
]
