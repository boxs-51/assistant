from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Any, Iterable

from sqlalchemy import or_, select

from ...infrastructure.storage.models.sql.agent import (
    AgentCheckpointPendingInvocationRecord,
    AgentExecutionCheckpointRecord,
    AgentExecutionRecord,
    AgentIterationRecord,
    AgentResumeClaimRecord,
    AgentTaskAggregateAdmissionRecord,
    AgentTaskBranchContextRecord,
    AgentTaskBranchRecord,
    AgentTaskForkAdmissionRecord,
    AgentTaskRecord,
    AgentTaskRetryAdmissionRecord,
    AgentToolCallRecord,
    AgentToolResultRecord,
    AgentTranscriptChunkRecord,
    AgentTranscriptPayloadNodeRecord,
    AgentTranscriptRepresentationRecord,
    TaskBudgetRecord,
    TaskBudgetReservationRecord,
)
from ...infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationAttemptRecord,
    CapabilityInvocationRecord,
)
from ..capability.contracts.invocation import TERMINAL_INVOCATION_STATES


_TERMINAL_TASK_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_ACTIVE_EXECUTION_STATES = frozenset(
    {"CREATED", "RUNNING", "WAITING", "WAITING_FOR_CONNECTION"}
)
_ACTIVE_RESUME_CLAIM_STATES = frozenset({"CREATED"})
_UNSAFE_REMOTE_OUTCOMES = frozenset({"IN_FLIGHT", "OUTCOME_UNKNOWN"})
_TERMINAL_INVOCATION_VALUES = frozenset(
    getattr(item, "value", str(item)) for item in TERMINAL_INVOCATION_STATES
)


class GcDryRunClassification(str, Enum):
    RETAIN = "RETAIN"
    CANDIDATE = "CANDIDATE"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True, order=True)
class GcRowKey:
    row_kind: str
    row_identity: str


@dataclass(frozen=True)
class GcDryRunItem:
    row_kind: str
    row_identity: str
    classification: GcDryRunClassification
    reason_code: str
    root_or_edge_source: str


@dataclass(frozen=True)
class GcDryRunReport:
    task_id: str
    policy_eligible_terminal: bool
    items: tuple[GcDryRunItem, ...]
    fingerprint: str

    @property
    def has_candidates(self) -> bool:
        return any(
            item.classification is GcDryRunClassification.CANDIDATE
            for item in self.items
        )

    @property
    def failed_closed(self) -> bool:
        return any(
            item.classification is GcDryRunClassification.FAIL_CLOSED
            for item in self.items
        )


def _identity(*parts: Any) -> str:
    return "|".join(str(part) for part in parts)


def _fingerprint(items: Iterable[GcDryRunItem]) -> str:
    payload = [
        {
            "row_kind": item.row_kind,
            "row_identity": item.row_identity,
            "classification": item.classification.value,
            "reason_code": item.reason_code,
            "root_or_edge_source": item.root_or_edge_source,
        }
        for item in items
    ]
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _reason_for_retained(kind: str, *, active_task: bool) -> str:
    if kind == "task":
        return "ACTIVE_EXECUTION" if active_task else "POLICY_RETAINED_HISTORY"
    if kind in {"task_budget", "task_budget_reservation"}:
        return "REPLAY_IDEMPOTENCY_AUTHORITY"
    if kind in {
        "branch",
        "branch_context",
        "resume_claim",
        "fork_admission",
        "retry_admission",
        "aggregate_admission",
    }:
        return "CLAIM_OR_ADMISSION_PROVENANCE"
    if kind in {"iteration", "tool_call", "tool_result", "checkpoint"}:
        return "EXECUTION_REPLAY_EVIDENCE"
    if kind == "checkpoint_pending_invocation":
        return "CHECKPOINT_PENDING_INVOCATION_AUTHORITY"
    if kind in {"capability_invocation", "capability_invocation_attempt"}:
        return "CAPABILITY_SIDE_EFFECT_AUTHORITY"
    if kind in {
        "transcript_representation",
        "transcript_payload",
        "transcript_chunk",
    }:
        return "TRANSCRIPT_REACHABILITY"
    if kind == "execution":
        return "EXECUTION_LINEAGE"
    return "POLICY_RETAINED_HISTORY"


async def _rows(session, statement):
    result = await session.execute(statement)
    return list(result.scalars().all())


async def build_task_gc_dry_run_in_uow(
    uow,
    task_id: str,
    *,
    policy_eligible_terminal: bool = False,
) -> GcDryRunReport:
    """Build one deterministic, read-only R11-F1-B Task GC dry-run.

    The caller owns the UoW. This function does not flush, commit, rollback,
    delete, update, or insert. All persistence reads occur through the same
    SQLAlchemy session/transaction.
    """

    session = uow.session
    rows: dict[GcRowKey, Any] = {}
    edges: dict[GcRowKey, list[tuple[str, GcRowKey]]] = defaultdict(list)
    problems: list[str] = []

    def add(kind: str, identity: str, value: Any) -> GcRowKey:
        key = GcRowKey(kind, str(identity))
        rows.setdefault(key, value)
        return key

    def add_edge(
        source: GcRowKey,
        target: GcRowKey | None,
        label: str,
        *,
        required: bool = True,
    ) -> None:
        if target is None:
            if required:
                problems.append(
                    f"{source.row_kind}:{source.row_identity}:{label}:missing"
                )
            return
        edges[source].append((label, target))

    task = await uow.agents.get_task(task_id)
    task_key = GcRowKey("task", task_id)
    if task is None:
        item = GcDryRunItem(
            row_kind="task",
            row_identity=task_id,
            classification=GcDryRunClassification.FAIL_CLOSED,
            reason_code="INCOMPLETE_OR_INCONSISTENT_GRAPH",
            root_or_edge_source="TASK_NOT_FOUND",
        )
        return GcDryRunReport(
            task_id=task_id,
            policy_eligible_terminal=policy_eligible_terminal,
            items=(item,),
            fingerprint=_fingerprint((item,)),
        )

    task_key = add("task", task.id, task)

    budget_rows = await _rows(
        session,
        select(TaskBudgetRecord).where(TaskBudgetRecord.task_id == task_id),
    )
    reservation_rows = await _rows(
        session,
        select(TaskBudgetReservationRecord).where(
            TaskBudgetReservationRecord.task_id == task_id
        ),
    )
    branch_rows = await _rows(
        session,
        select(AgentTaskBranchRecord).where(
            AgentTaskBranchRecord.task_id == task_id
        ),
    )
    execution_rows = await _rows(
        session,
        select(AgentExecutionRecord).where(
            AgentExecutionRecord.task_id == task_id
        ),
    )
    fork_rows = await _rows(
        session,
        select(AgentTaskForkAdmissionRecord).where(
            AgentTaskForkAdmissionRecord.task_id == task_id
        ),
    )
    retry_rows = await _rows(
        session,
        select(AgentTaskRetryAdmissionRecord).where(
            AgentTaskRetryAdmissionRecord.task_id == task_id
        ),
    )
    aggregate_rows = await _rows(
        session,
        select(AgentTaskAggregateAdmissionRecord).where(
            AgentTaskAggregateAdmissionRecord.task_id == task_id
        ),
    )

    if len(budget_rows) != 1:
        problems.append("task_budget:missing_or_duplicated")

    budget_by_task = {row.task_id: row for row in budget_rows}
    reservation_keys: dict[tuple[str, str, str], GcRowKey] = {}
    branch_keys: dict[str, GcRowKey] = {}
    execution_keys: dict[str, GcRowKey] = {}
    fork_keys: dict[tuple[str, str], GcRowKey] = {}
    retry_keys: dict[tuple[str, str], GcRowKey] = {}
    aggregate_keys: dict[tuple[str, str], GcRowKey] = {}

    for row in budget_rows:
        key = add("task_budget", row.task_id, row)
        add_edge(task_key, key, "task_budget")
    for row in reservation_rows:
        ident = (row.task_id, row.kind, row.reservation_key)
        key = add("task_budget_reservation", _identity(*ident), row)
        reservation_keys[ident] = key
        add_edge(task_key, key, "task_budget_reservation")
    for row in branch_rows:
        key = add("branch", row.branch_id, row)
        branch_keys[row.branch_id] = key
        add_edge(task_key, key, "branch")
    for row in execution_rows:
        key = add("execution", row.id, row)
        execution_keys[row.id] = key
        add_edge(task_key, key, "execution")
    for row in fork_rows:
        ident = (row.task_id, row.fork_request_id)
        key = add("fork_admission", _identity(*ident), row)
        fork_keys[ident] = key
        add_edge(task_key, key, "fork_admission")
    for row in retry_rows:
        ident = (row.task_id, row.retry_request_id)
        key = add("retry_admission", _identity(*ident), row)
        retry_keys[ident] = key
        add_edge(task_key, key, "retry_admission")
    for row in aggregate_rows:
        ident = (row.task_id, row.aggregate_request_id)
        key = add("aggregate_admission", _identity(*ident), row)
        aggregate_keys[ident] = key
        add_edge(task_key, key, "aggregate_admission")

    execution_ids = tuple(sorted(execution_keys))
    branch_ids = tuple(sorted(branch_keys))

    branch_context_rows = (
        await _rows(
            session,
            select(AgentTaskBranchContextRecord).where(
                AgentTaskBranchContextRecord.branch_id.in_(branch_ids)
            ),
        )
        if branch_ids
        else []
    )
    branch_context_keys: dict[str, GcRowKey] = {}
    for row in branch_context_rows:
        key = add("branch_context", row.branch_id, row)
        branch_context_keys[row.branch_id] = key
        add_edge(
            branch_keys.get(row.branch_id, task_key),
            key,
            "branch_context",
        )

    checkpoint_rows = (
        await _rows(
            session,
            select(AgentExecutionCheckpointRecord).where(
                or_(
                    AgentExecutionCheckpointRecord.task_id == task_id,
                    AgentExecutionCheckpointRecord.execution_id.in_(
                        execution_ids
                    ),
                )
            ),
        )
        if execution_ids
        else await _rows(
            session,
            select(AgentExecutionCheckpointRecord).where(
                AgentExecutionCheckpointRecord.task_id == task_id
            ),
        )
    )
    checkpoint_keys: dict[str, GcRowKey] = {}
    checkpoints_by_execution: dict[str, list[GcRowKey]] = defaultdict(list)
    checkpoint_by_id: dict[str, Any] = {}
    for row in checkpoint_rows:
        key = add("checkpoint", row.checkpoint_id, row)
        checkpoint_keys[row.checkpoint_id] = key
        checkpoint_by_id[row.checkpoint_id] = row
        checkpoints_by_execution[row.execution_id].append(key)

    checkpoint_ids = tuple(sorted(checkpoint_keys))
    pending_rows = (
        await _rows(
            session,
            select(AgentCheckpointPendingInvocationRecord).where(
                AgentCheckpointPendingInvocationRecord.checkpoint_id.in_(
                    checkpoint_ids
                )
            ),
        )
        if checkpoint_ids
        else []
    )
    pending_keys: dict[tuple[str, int], GcRowKey] = {}
    for row in pending_rows:
        ident = (row.checkpoint_id, int(row.ordinal))
        key = add(
            "checkpoint_pending_invocation",
            _identity(*ident),
            row,
        )
        pending_keys[ident] = key
        add_edge(
            checkpoint_keys.get(row.checkpoint_id, task_key),
            key,
            "checkpoint_pending_invocation",
        )

    claim_rows = (
        await _rows(
            session,
            select(AgentResumeClaimRecord).where(
                AgentResumeClaimRecord.execution_id.in_(execution_ids)
            ),
        )
        if execution_ids
        else []
    )
    claim_keys: dict[str, GcRowKey] = {}
    claims_by_execution: dict[str, list[GcRowKey]] = defaultdict(list)
    for row in claim_rows:
        key = add("resume_claim", row.claim_id, row)
        claim_keys[row.claim_id] = key
        claims_by_execution[row.execution_id].append(key)

    iteration_rows = (
        await _rows(
            session,
            select(AgentIterationRecord).where(
                AgentIterationRecord.execution_id.in_(execution_ids)
            ),
        )
        if execution_ids
        else []
    )
    iteration_keys: dict[str, GcRowKey] = {}
    iterations_by_execution: dict[str, list[GcRowKey]] = defaultdict(list)
    for row in iteration_rows:
        key = add("iteration", row.id, row)
        iteration_keys[row.id] = key
        iterations_by_execution[row.execution_id].append(key)

    tool_call_rows = (
        await _rows(
            session,
            select(AgentToolCallRecord).where(
                AgentToolCallRecord.execution_id.in_(execution_ids)
            ),
        )
        if execution_ids
        else []
    )
    tool_result_rows = (
        await _rows(
            session,
            select(AgentToolResultRecord).where(
                AgentToolResultRecord.execution_id.in_(execution_ids)
            ),
        )
        if execution_ids
        else []
    )
    tool_call_keys: dict[str, GcRowKey] = {}
    tool_result_keys: dict[str, GcRowKey] = {}
    tool_calls_by_iteration: dict[str, list[GcRowKey]] = defaultdict(list)
    tool_results_by_iteration: dict[str, list[GcRowKey]] = defaultdict(list)
    referenced_invocation_ids: set[str] = set()
    for row in tool_call_rows:
        key = add("tool_call", row.id, row)
        tool_call_keys[row.id] = key
        tool_calls_by_iteration[row.iteration_id].append(key)
        referenced_invocation_ids.add(str(row.invocation_id))
    for row in tool_result_rows:
        key = add("tool_result", row.id, row)
        tool_result_keys[row.id] = key
        tool_results_by_iteration[row.iteration_id].append(key)
        referenced_invocation_ids.add(str(row.invocation_id))
    for row in pending_rows:
        referenced_invocation_ids.add(str(row.invocation_id))

    invocation_predicates = []
    if execution_ids:
        invocation_predicates.append(
            CapabilityInvocationRecord.execution_id.in_(execution_ids)
        )
    if referenced_invocation_ids:
        invocation_predicates.append(
            CapabilityInvocationRecord.invocation_id.in_(
                tuple(sorted(referenced_invocation_ids))
            )
        )
    invocation_rows = (
        await _rows(
            session,
            select(CapabilityInvocationRecord).where(
                or_(*invocation_predicates)
            ),
        )
        if invocation_predicates
        else []
    )
    invocation_keys: dict[str, GcRowKey] = {}
    invocation_by_id: dict[str, Any] = {}
    for row in invocation_rows:
        key = add("capability_invocation", row.invocation_id, row)
        invocation_keys[row.invocation_id] = key
        invocation_by_id[row.invocation_id] = row

    invocation_ids = tuple(sorted(invocation_keys))
    attempt_rows = (
        await _rows(
            session,
            select(CapabilityInvocationAttemptRecord).where(
                CapabilityInvocationAttemptRecord.invocation_id.in_(
                    invocation_ids
                )
            ),
        )
        if invocation_ids
        else []
    )
    attempts_by_invocation: dict[str, list[GcRowKey]] = defaultdict(list)
    for row in attempt_rows:
        key = add("capability_invocation_attempt", row.attempt_id, row)
        attempts_by_invocation[row.invocation_id].append(key)

    # Semantic side-effect identity must agree with every Agent-side durable
    # reference. A matching invocation_id alone is not enough authority.
    def validate_invocation_identity(
        *,
        invocation_id: str,
        expected_execution_id: str,
        expected_tool_call_id: str,
        expected_capability_id: str,
        source: str,
    ) -> None:
        invocation = invocation_by_id.get(str(invocation_id))
        if invocation is None:
            return
        if str(invocation.execution_id) != str(expected_execution_id):
            problems.append(f"{source}:invocation_execution_mismatch")
        if str(invocation.tool_call_id) != str(expected_tool_call_id):
            problems.append(f"{source}:invocation_tool_call_mismatch")
        if str(invocation.capability_id) != str(expected_capability_id):
            problems.append(f"{source}:invocation_capability_mismatch")

    for row in tool_call_rows:
        validate_invocation_identity(
            invocation_id=str(row.invocation_id),
            expected_execution_id=str(row.execution_id),
            expected_tool_call_id=str(row.tool_call_id),
            expected_capability_id=str(row.capability_id),
            source=f"tool_call:{row.id}",
        )
    for row in tool_result_rows:
        validate_invocation_identity(
            invocation_id=str(row.invocation_id),
            expected_execution_id=str(row.execution_id),
            expected_tool_call_id=str(row.tool_call_id),
            expected_capability_id=str(row.capability_id),
            source=f"tool_result:{row.id}",
        )
    for row in pending_rows:
        checkpoint = checkpoint_by_id.get(row.checkpoint_id)
        if checkpoint is None:
            continue
        validate_invocation_identity(
            invocation_id=str(row.invocation_id),
            expected_execution_id=str(checkpoint.execution_id),
            expected_tool_call_id=str(row.tool_call_id),
            expected_capability_id=str(row.capability_id),
            source=(
                "checkpoint_pending_invocation:"
                f"{row.checkpoint_id}|{int(row.ordinal)}"
            ),
        )

    # Reverse/inbound semantic authority is required because Agent-side
    # invocation_id columns are strings, not FKs to capability_invocations.
    # A candidate CapabilityInvocation may not be removed while any retained
    # Agent graph outside this Task still names the same invocation_id.
    if invocation_ids:
        external_tool_call_query = select(AgentToolCallRecord).where(
            AgentToolCallRecord.invocation_id.in_(invocation_ids)
        )
        external_tool_result_query = select(AgentToolResultRecord).where(
            AgentToolResultRecord.invocation_id.in_(invocation_ids)
        )
        if execution_ids:
            external_tool_call_query = external_tool_call_query.where(
                ~AgentToolCallRecord.execution_id.in_(execution_ids)
            )
            external_tool_result_query = external_tool_result_query.where(
                ~AgentToolResultRecord.execution_id.in_(execution_ids)
            )

        external_tool_call_rows = await _rows(
            session,
            external_tool_call_query,
        )
        if external_tool_call_rows:
            problems.append(
                "external_agent_tool_call_invocation_reference"
            )

        external_tool_result_rows = await _rows(
            session,
            external_tool_result_query,
        )
        if external_tool_result_rows:
            problems.append(
                "external_agent_tool_result_invocation_reference"
            )

        external_pending_query = select(
            AgentCheckpointPendingInvocationRecord
        ).where(
            AgentCheckpointPendingInvocationRecord.invocation_id.in_(
                invocation_ids
            )
        )
        if checkpoint_ids:
            external_pending_query = external_pending_query.where(
                ~AgentCheckpointPendingInvocationRecord.checkpoint_id.in_(
                    checkpoint_ids
                )
            )

        external_pending_rows = await _rows(
            session,
            external_pending_query,
        )
        if external_pending_rows:
            problems.append(
                "external_checkpoint_pending_invocation_reference"
            )

    # Build frozen task-owned edges.
    for row in branch_rows:
        source = branch_keys[row.branch_id]
        if row.parent_branch_id is not None:
            add_edge(
                source,
                branch_keys.get(row.parent_branch_id),
                "parent_branch",
            )
        if row.base_execution_id is not None:
            add_edge(
                source,
                execution_keys.get(row.base_execution_id),
                "base_execution",
            )
        if row.base_checkpoint_id is not None:
            add_edge(
                source,
                checkpoint_keys.get(row.base_checkpoint_id),
                "base_checkpoint",
            )
        if row.current_execution_id is not None:
            add_edge(
                source,
                execution_keys.get(row.current_execution_id),
                "current_execution",
            )

    for row in execution_rows:
        source = execution_keys[row.id]
        for target_id, label in (
            (row.parent_execution_id, "parent_execution"),
            (row.retry_of_execution_id, "retry_of_execution"),
            (row.base_execution_id, "base_execution"),
        ):
            if target_id is not None:
                add_edge(
                    source,
                    execution_keys.get(target_id),
                    label,
                )
        if row.base_checkpoint_id is not None:
            add_edge(
                source,
                checkpoint_keys.get(row.base_checkpoint_id),
                "base_checkpoint",
            )
        if row.current_checkpoint_id is not None:
            add_edge(
                source,
                checkpoint_keys.get(row.current_checkpoint_id),
                "current_checkpoint",
            )
        for key in sorted(checkpoints_by_execution[row.id]):
            add_edge(source, key, "checkpoint")
        for key in sorted(iterations_by_execution[row.id]):
            add_edge(source, key, "iteration")
        for key in sorted(claims_by_execution[row.id]):
            add_edge(source, key, "resume_claim")
        for inv_id, inv_key in sorted(invocation_keys.items()):
            inv = rows[inv_key]
            if inv.execution_id == row.id:
                add_edge(source, inv_key, "capability_invocation")

    for row in checkpoint_rows:
        source = checkpoint_keys[row.checkpoint_id]
        if row.execution_id not in execution_keys:
            problems.append(
                f"checkpoint:{row.checkpoint_id}:foreign_or_missing_execution"
            )
        else:
            add_edge(
                execution_keys[row.execution_id],
                source,
                "checkpoint",
            )
        if row.parent_checkpoint_id is not None:
            add_edge(
                source,
                checkpoint_keys.get(row.parent_checkpoint_id),
                "parent_checkpoint",
            )

    for row in claim_rows:
        source = claim_keys[row.claim_id]
        add_edge(
            source,
            execution_keys.get(row.execution_id),
            "claim_execution",
        )
        add_edge(
            source,
            checkpoint_keys.get(row.checkpoint_id),
            "claim_checkpoint",
        )

    for row in iteration_rows:
        source = iteration_keys[row.id]
        add_edge(
            execution_keys.get(row.execution_id, task_key),
            source,
            "iteration",
        )
        for key in sorted(tool_calls_by_iteration[row.id]):
            add_edge(source, key, "tool_call")
        for key in sorted(tool_results_by_iteration[row.id]):
            add_edge(source, key, "tool_result")

    for row in tool_call_rows:
        source = tool_call_keys[row.id]
        add_edge(
            source,
            invocation_keys.get(str(row.invocation_id)),
            "tool_call_invocation",
        )
    for row in tool_result_rows:
        source = tool_result_keys[row.id]
        add_edge(
            source,
            invocation_keys.get(str(row.invocation_id)),
            "tool_result_invocation",
        )
    for row in pending_rows:
        source = pending_keys[(row.checkpoint_id, int(row.ordinal))]
        add_edge(
            source,
            invocation_keys.get(str(row.invocation_id)),
            "pending_invocation",
        )
    for row in invocation_rows:
        source = invocation_keys[row.invocation_id]
        for key in sorted(attempts_by_invocation[row.invocation_id]):
            add_edge(source, key, "invocation_attempt")

    for row in fork_rows:
        source = fork_keys[(row.task_id, row.fork_request_id)]
        for target, label, mapping in (
            (row.source_branch_id, "source_branch", branch_keys),
            (row.branch_id, "branch", branch_keys),
            (row.source_execution_id, "source_execution", execution_keys),
            (row.execution_id, "execution", execution_keys),
            (row.source_checkpoint_id, "source_checkpoint", checkpoint_keys),
        ):
            add_edge(source, mapping.get(target), label)

    for row in retry_rows:
        source = retry_keys[(row.task_id, row.retry_request_id)]
        for target, label, mapping in (
            (row.branch_id, "branch", branch_keys),
            (row.source_execution_id, "source_execution", execution_keys),
            (row.execution_id, "execution", execution_keys),
        ):
            add_edge(source, mapping.get(target), label)
        if row.source_checkpoint_id is not None:
            add_edge(
                source,
                checkpoint_keys.get(row.source_checkpoint_id),
                "source_checkpoint",
            )

    for row in aggregate_rows:
        source = aggregate_keys[(row.task_id, row.aggregate_request_id)]
        add_edge(
            source,
            branch_keys.get(row.target_branch_id),
            "target_branch",
        )
        add_edge(
            source,
            execution_keys.get(row.execution_id),
            "execution",
        )
        branch_snapshots = row.source_branch_snapshots
        execution_snapshots = row.source_execution_snapshots
        if not isinstance(branch_snapshots, list) or not isinstance(
            execution_snapshots, list
        ):
            problems.append(
                f"aggregate_admission:{row.aggregate_request_id}:malformed_snapshots"
            )
            continue
        for snapshot in branch_snapshots:
            if not isinstance(snapshot, dict) or "branch_id" not in snapshot:
                problems.append(
                    f"aggregate_admission:{row.aggregate_request_id}:"
                    "malformed_branch_snapshot"
                )
                continue
            add_edge(
                source,
                branch_keys.get(str(snapshot["branch_id"])),
                "aggregate_source_branch",
            )
        for snapshot in execution_snapshots:
            if not isinstance(snapshot, dict) or "execution_id" not in snapshot:
                problems.append(
                    f"aggregate_admission:{row.aggregate_request_id}:"
                    "malformed_execution_snapshot"
                )
                continue
            add_edge(
                source,
                execution_keys.get(str(snapshot["execution_id"])),
                "aggregate_source_execution",
            )
            checkpoint_id = snapshot.get("checkpoint_id")
            if checkpoint_id is not None:
                add_edge(
                    source,
                    checkpoint_keys.get(str(checkpoint_id)),
                    "aggregate_source_checkpoint",
                )

    # Detect execution/checkpoint semantic cycles.
    def detect_cycle(
        graph: dict[str, tuple[str, ...]],
        family: str,
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for target in sorted(graph.get(node, ())):
                if target in graph and visit(target):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        for node in sorted(graph):
            if visit(node):
                problems.append(f"{family}:cycle")
                return

    execution_lineage_graph = {
        row.id: tuple(
            target
            for target in (
                row.parent_execution_id,
                row.retry_of_execution_id,
                row.base_execution_id,
            )
            if target is not None
        )
        for row in execution_rows
    }
    checkpoint_lineage_graph = {
        row.checkpoint_id: (
            (row.parent_checkpoint_id,)
            if row.parent_checkpoint_id is not None
            else ()
        )
        for row in checkpoint_rows
    }
    detect_cycle(execution_lineage_graph, "execution_lineage")
    detect_cycle(checkpoint_lineage_graph, "checkpoint_lineage")

    # External semantic/FK provenance into a candidate Task is a fail-closed
    # boundary for this task-scoped slice. NULL task_id is explicitly foreign;
    # SQL `!=` alone would otherwise miss it via three-valued logic.
    external_task = or_(
        AgentExecutionRecord.task_id.is_(None),
        AgentExecutionRecord.task_id != task_id,
    )
    if execution_ids:
        inbound_execution_rows = await _rows(
            session,
            select(AgentExecutionRecord).where(
                external_task,
                or_(
                    AgentExecutionRecord.parent_execution_id.in_(execution_ids),
                    AgentExecutionRecord.retry_of_execution_id.in_(execution_ids),
                    AgentExecutionRecord.base_execution_id.in_(execution_ids),
                ),
            ),
        )
        if inbound_execution_rows:
            problems.append("external_execution_semantic_reference")

        inbound_branch_execution_rows = await _rows(
            session,
            select(AgentTaskBranchRecord).where(
                AgentTaskBranchRecord.task_id != task_id,
                or_(
                    AgentTaskBranchRecord.base_execution_id.in_(execution_ids),
                    AgentTaskBranchRecord.current_execution_id.in_(execution_ids),
                ),
            ),
        )
        if inbound_branch_execution_rows:
            problems.append("external_branch_execution_reference")

        inbound_fork_rows = await _rows(
            session,
            select(AgentTaskForkAdmissionRecord).where(
                AgentTaskForkAdmissionRecord.task_id != task_id,
                or_(
                    AgentTaskForkAdmissionRecord.source_execution_id.in_(
                        execution_ids
                    ),
                    AgentTaskForkAdmissionRecord.execution_id.in_(execution_ids),
                ),
            ),
        )
        if inbound_fork_rows:
            problems.append("external_fork_execution_reference")

        inbound_retry_rows = await _rows(
            session,
            select(AgentTaskRetryAdmissionRecord).where(
                AgentTaskRetryAdmissionRecord.task_id != task_id,
                or_(
                    AgentTaskRetryAdmissionRecord.source_execution_id.in_(
                        execution_ids
                    ),
                    AgentTaskRetryAdmissionRecord.execution_id.in_(execution_ids),
                ),
            ),
        )
        if inbound_retry_rows:
            problems.append("external_retry_execution_reference")

    if branch_ids:
        inbound_branch_lineage_rows = await _rows(
            session,
            select(AgentTaskBranchRecord).where(
                AgentTaskBranchRecord.task_id != task_id,
                AgentTaskBranchRecord.parent_branch_id.in_(branch_ids),
            ),
        )
        if inbound_branch_lineage_rows:
            problems.append("external_branch_lineage_reference")

        inbound_fork_branch_rows = await _rows(
            session,
            select(AgentTaskForkAdmissionRecord).where(
                AgentTaskForkAdmissionRecord.task_id != task_id,
                or_(
                    AgentTaskForkAdmissionRecord.source_branch_id.in_(branch_ids),
                    AgentTaskForkAdmissionRecord.branch_id.in_(branch_ids),
                ),
            ),
        )
        if inbound_fork_branch_rows:
            problems.append("external_fork_branch_reference")

        inbound_retry_branch_rows = await _rows(
            session,
            select(AgentTaskRetryAdmissionRecord).where(
                AgentTaskRetryAdmissionRecord.task_id != task_id,
                AgentTaskRetryAdmissionRecord.branch_id.in_(branch_ids),
            ),
        )
        if inbound_retry_branch_rows:
            problems.append("external_retry_branch_reference")

    if checkpoint_ids:
        inbound_checkpoint_rows = await _rows(
            session,
            select(AgentExecutionRecord).where(
                external_task,
                AgentExecutionRecord.base_checkpoint_id.in_(checkpoint_ids),
            ),
        )
        if inbound_checkpoint_rows:
            problems.append("external_checkpoint_semantic_reference")

        inbound_branch_checkpoint_rows = await _rows(
            session,
            select(AgentTaskBranchRecord).where(
                AgentTaskBranchRecord.task_id != task_id,
                AgentTaskBranchRecord.base_checkpoint_id.in_(checkpoint_ids),
            ),
        )
        if inbound_branch_checkpoint_rows:
            problems.append("external_branch_checkpoint_reference")

        inbound_fork_checkpoint_rows = await _rows(
            session,
            select(AgentTaskForkAdmissionRecord).where(
                AgentTaskForkAdmissionRecord.task_id != task_id,
                AgentTaskForkAdmissionRecord.source_checkpoint_id.in_(
                    checkpoint_ids
                ),
            ),
        )
        if inbound_fork_checkpoint_rows:
            problems.append("external_fork_checkpoint_reference")

        inbound_retry_checkpoint_rows = await _rows(
            session,
            select(AgentTaskRetryAdmissionRecord).where(
                AgentTaskRetryAdmissionRecord.task_id != task_id,
                AgentTaskRetryAdmissionRecord.source_checkpoint_id.in_(
                    checkpoint_ids
                ),
            ),
        )
        if inbound_retry_checkpoint_rows:
            problems.append("external_retry_checkpoint_reference")

    # Aggregate source snapshots are semantic JSON authority, so they need an
    # explicit cross-Task scan rather than relying on SQL FK traversal.
    if execution_ids or branch_ids or checkpoint_ids:
        inbound_aggregate_rows = await _rows(
            session,
            select(AgentTaskAggregateAdmissionRecord).where(
                AgentTaskAggregateAdmissionRecord.task_id != task_id
            ),
        )
        candidate_execution_ids = set(execution_ids)
        candidate_branch_ids = set(branch_ids)
        candidate_checkpoint_ids = set(checkpoint_ids)
        for row in inbound_aggregate_rows:
            if (
                row.execution_id in candidate_execution_ids
                or row.target_branch_id in candidate_branch_ids
            ):
                problems.append("external_aggregate_direct_reference")
                break
            branch_snapshots = row.source_branch_snapshots or []
            execution_snapshots = row.source_execution_snapshots or []
            if any(
                isinstance(item, dict)
                and str(item.get("branch_id")) in candidate_branch_ids
                for item in branch_snapshots
            ):
                problems.append("external_aggregate_branch_reference")
                break
            for item in execution_snapshots:
                if not isinstance(item, dict):
                    continue
                if str(item.get("execution_id")) in candidate_execution_ids:
                    problems.append("external_aggregate_execution_reference")
                    break
                checkpoint_id = item.get("checkpoint_id")
                if (
                    checkpoint_id is not None
                    and str(checkpoint_id) in candidate_checkpoint_ids
                ):
                    problems.append("external_aggregate_checkpoint_reference")
                    break
            else:
                continue
            break

    # Transcript closure is deliberately retain-only in task-scoped F1-B.
    transcript_root_keys: list[GcRowKey] = []
    transcript_rep_keys: dict[tuple[str, int], GcRowKey] = {}
    transcript_payload_keys: dict[str, GcRowKey] = {}
    transcript_chunk_keys: dict[str, GcRowKey] = {}
    transcript_rep_visiting: set[tuple[str, int]] = set()
    transcript_payload_visiting: set[str] = set()

    async def load_payload(payload_root_ref: str) -> GcRowKey | None:
        if payload_root_ref in transcript_payload_keys:
            return transcript_payload_keys[payload_root_ref]
        if payload_root_ref in transcript_payload_visiting:
            problems.append("transcript_payload:cycle")
            return None
        transcript_payload_visiting.add(payload_root_ref)
        payload_rows = await _rows(
            session,
            select(AgentTranscriptPayloadNodeRecord).where(
                AgentTranscriptPayloadNodeRecord.payload_root_ref
                == payload_root_ref
            ),
        )
        if len(payload_rows) != 1:
            problems.append(
                f"transcript_payload:{payload_root_ref}:missing_or_duplicated"
            )
            transcript_payload_visiting.remove(payload_root_ref)
            return None
        row = payload_rows[0]
        key = add("transcript_payload", row.payload_root_ref, row)
        transcript_payload_keys[row.payload_root_ref] = key

        chunk_rows = await _rows(
            session,
            select(AgentTranscriptChunkRecord).where(
                AgentTranscriptChunkRecord.chunk_id == row.chunk_id
            ),
        )
        if len(chunk_rows) != 1:
            problems.append(
                f"transcript_chunk:{row.chunk_id}:missing_or_duplicated"
            )
        else:
            chunk = chunk_rows[0]
            chunk_key = add("transcript_chunk", chunk.chunk_id, chunk)
            transcript_chunk_keys[chunk.chunk_id] = chunk_key
            add_edge(key, chunk_key, "payload_chunk")

        if row.parent_payload_root_ref is not None:
            parent = await load_payload(row.parent_payload_root_ref)
            add_edge(key, parent, "parent_payload")
        transcript_payload_visiting.remove(payload_root_ref)
        return key

    async def load_representation(
        transcript_ref: str,
        transcript_version: int,
    ) -> GcRowKey | None:
        identity = (transcript_ref, int(transcript_version))
        if identity in transcript_rep_keys:
            return transcript_rep_keys[identity]
        if identity in transcript_rep_visiting:
            problems.append("transcript_representation:cycle")
            return None
        transcript_rep_visiting.add(identity)
        rep_rows = await _rows(
            session,
            select(AgentTranscriptRepresentationRecord).where(
                AgentTranscriptRepresentationRecord.transcript_ref
                == transcript_ref,
                AgentTranscriptRepresentationRecord.transcript_version
                == int(transcript_version),
            ),
        )
        if len(rep_rows) != 1:
            problems.append(
                "transcript_representation:"
                f"{transcript_ref}@{transcript_version}:missing_or_duplicated"
            )
            transcript_rep_visiting.remove(identity)
            return None
        row = rep_rows[0]
        key = add(
            "transcript_representation",
            _identity(row.transcript_ref, int(row.transcript_version)),
            row,
        )
        transcript_rep_keys[identity] = key

        payload_key = await load_payload(row.payload_root_ref)
        add_edge(key, payload_key, "representation_payload")
        if row.parent_transcript_ref is not None:
            if row.parent_transcript_version is None:
                problems.append(
                    "transcript_representation:"
                    f"{transcript_ref}@{transcript_version}:missing_parent_version"
                )
            else:
                parent_key = await load_representation(
                    row.parent_transcript_ref,
                    int(row.parent_transcript_version),
                )
                add_edge(key, parent_key, "parent_representation")
        transcript_rep_visiting.remove(identity)
        return key

    for checkpoint in sorted(
        checkpoint_rows,
        key=lambda row: row.checkpoint_id,
    ):
        if checkpoint.transcript_ref is None:
            if checkpoint.transcript_version is not None:
                problems.append(
                    f"checkpoint:{checkpoint.checkpoint_id}:"
                    "transcript_version_without_ref"
                )
            continue
        if checkpoint.transcript_version is None:
            problems.append(
                f"checkpoint:{checkpoint.checkpoint_id}:"
                "transcript_ref_without_version"
            )
            continue
        rep_key = await load_representation(
            checkpoint.transcript_ref,
            int(checkpoint.transcript_version),
        )
        add_edge(
            checkpoint_keys[checkpoint.checkpoint_id],
            rep_key,
            "checkpoint_transcript",
        )
        if rep_key is not None:
            transcript_root_keys.append(rep_key)

    # Policy eligibility must be explicit and whole-task safe.
    task_status = str(task.status)
    active_task = task_status not in _TERMINAL_TASK_STATES
    if policy_eligible_terminal:
        if active_task:
            problems.append("policy_eligible_nonterminal_task")
        budget = budget_by_task.get(task_id)
        if budget is None or str(budget.state) != "CLOSED":
            problems.append("policy_eligible_task_budget_not_closed")
        if any(
            str(row.state) in _ACTIVE_EXECUTION_STATES
            for row in execution_rows
        ):
            problems.append("policy_eligible_active_execution")
        if any(
            str(row.state) in _ACTIVE_RESUME_CLAIM_STATES
            for row in claim_rows
        ):
            problems.append("policy_eligible_active_resume_claim")
        for row in tool_result_rows:
            if str(row.commit_state) != "COMMITTED":
                problems.append(
                    f"tool_result:{row.id}:non_committed_authority"
                )
        for row in invocation_rows:
            state = str(row.state)
            remote = (
                None
                if row.remote_outcome_state is None
                else str(row.remote_outcome_state)
            )
            if state not in _TERMINAL_INVOCATION_VALUES:
                problems.append(
                    f"capability_invocation:{row.invocation_id}:nonterminal"
                )
            if remote in _UNSAFE_REMOTE_OUTCOMES:
                problems.append(
                    f"capability_invocation:{row.invocation_id}:"
                    f"unsafe_remote_outcome:{remote}"
                )
        for row in attempt_rows:
            if str(row.state) not in _TERMINAL_INVOCATION_VALUES:
                problems.append(
                    f"capability_invocation_attempt:{row.attempt_id}:"
                    "nonterminal"
                )

    # Referenced invocation authority may never be absent.
    for invocation_id in sorted(referenced_invocation_ids):
        if invocation_id not in invocation_keys:
            problems.append(
                f"capability_invocation:{invocation_id}:missing_referenced_authority"
            )

    if problems:
        source = ";".join(sorted(set(problems)))
        items = tuple(
            GcDryRunItem(
                row_kind=key.row_kind,
                row_identity=key.row_identity,
                classification=GcDryRunClassification.FAIL_CLOSED,
                reason_code="INCOMPLETE_OR_INCONSISTENT_GRAPH",
                root_or_edge_source=source,
            )
            for key in sorted(rows)
        )
        return GcDryRunReport(
            task_id=task_id,
            policy_eligible_terminal=policy_eligible_terminal,
            items=items,
            fingerprint=_fingerprint(items),
        )

    roots: list[tuple[GcRowKey, str]] = []
    if not policy_eligible_terminal:
        roots.append(
            (
                task_key,
                "ACTIVE_TASK" if active_task else "POLICY_RETAINED_HISTORY",
            )
        )
    # Task-scoped F1-B never claims transcript storage; reached transcript
    # closure is independently rooted and always retained.
    for key in sorted(set(transcript_root_keys)):
        roots.append((key, "SHARED_TRANSCRIPT_REACHABILITY"))

    reached: dict[GcRowKey, str] = {}
    queue: deque[GcRowKey] = deque()
    for key, source in sorted(roots, key=lambda item: item[0]):
        if key not in reached:
            reached[key] = source
            queue.append(key)

    while queue:
        source = queue.popleft()
        for label, target in sorted(
            edges.get(source, ()),
            key=lambda item: (item[0], item[1]),
        ):
            if target not in reached:
                reached[target] = (
                    f"{source.row_kind}:{source.row_identity}"
                    f"--{label}-->"
                    f"{target.row_kind}:{target.row_identity}"
                )
                queue.append(target)

    items_list: list[GcDryRunItem] = []
    for key in sorted(rows):
        if key in reached:
            items_list.append(
                GcDryRunItem(
                    row_kind=key.row_kind,
                    row_identity=key.row_identity,
                    classification=GcDryRunClassification.RETAIN,
                    reason_code=_reason_for_retained(
                        key.row_kind,
                        active_task=active_task,
                    ),
                    root_or_edge_source=reached[key],
                )
            )
        else:
            items_list.append(
                GcDryRunItem(
                    row_kind=key.row_kind,
                    row_identity=key.row_identity,
                    classification=GcDryRunClassification.CANDIDATE,
                    reason_code="UNREACHABLE_POLICY_ELIGIBLE",
                    root_or_edge_source="EXPLICIT_TERMINAL_POLICY_ELIGIBILITY",
                )
            )

    items = tuple(items_list)
    return GcDryRunReport(
        task_id=task_id,
        policy_eligible_terminal=policy_eligible_terminal,
        items=items,
        fingerprint=_fingerprint(items),
    )


class AgentGcDryRunService:
    """Read-only convenience boundary around the transaction-scoped builder."""

    def __init__(self, uow_factory) -> None:
        self._uow_factory = uow_factory

    async def classify_task(
        self,
        task_id: str,
        *,
        policy_eligible_terminal: bool = False,
    ) -> GcDryRunReport:
        async with self._uow_factory() as uow:
            return await build_task_gc_dry_run_in_uow(
                uow,
                task_id,
                policy_eligible_terminal=policy_eligible_terminal,
            )
