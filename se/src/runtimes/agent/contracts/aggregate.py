from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING, Any, Mapping, Sequence

if TYPE_CHECKING:
    from .context import AgentExecutionContext


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def aggregate_fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def aggregate_plan_fingerprint(
    *,
    task_id: str,
    target_branch_id: str,
    source_branch_snapshots: Sequence[Mapping[str, Any]],
    source_execution_snapshots: Sequence[Mapping[str, Any]],
    result_fingerprints: Sequence[str],
    runtime_seed_fingerprint: str,
    created_by: str,
) -> str:
    return aggregate_fingerprint(
        {
            "task_id": task_id,
            "target_branch_id": target_branch_id,
            "source_branch_snapshots": [
                dict(item) for item in source_branch_snapshots
            ],
            "source_execution_snapshots": [
                dict(item) for item in source_execution_snapshots
            ],
            "result_fingerprints": list(result_fingerprints),
            "runtime_seed_fingerprint": runtime_seed_fingerprint,
            "created_by": created_by,
        }
    )


@dataclass(frozen=True, slots=True)
class AggregateAdmission:
    task_id: str
    aggregate_request_id: str
    plan_fingerprint: str
    runtime_seed_fingerprint: str
    target_branch_id: str
    branch_revision: int
    execution_id: str
    execution_revision: int
    source_branch_ids: tuple[str, ...]
    task_revision: int
    task_budget_revision: int


@dataclass(frozen=True, slots=True)
class AggregateExecutionBootstrap:
    execution_id: str
    expected_execution_revision: int
    task_id: str
    target_branch_id: str
    aggregate_request_id: str
    plan_fingerprint: str
    runtime_seed_fingerprint: str
    context: "AgentExecutionContext"


@dataclass(frozen=True, slots=True)
class AggregateActivationResult:
    task_id: str
    target_branch_id: str
    execution_id: str
    source_execution_revision: int
    activated_execution_revision: int
    remaining_active_budget_seconds: float


__all__ = [
    "AggregateActivationResult",
    "AggregateAdmission",
    "AggregateExecutionBootstrap",
    "aggregate_fingerprint",
    "aggregate_plan_fingerprint",
]
