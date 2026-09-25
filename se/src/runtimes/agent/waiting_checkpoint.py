from __future__ import annotations

from typing import Any, Sequence

from ...infrastructure.storage.transcript_representation import (
    canonical_transcript_messages,
)
from .checkpoint_transcript import (
    CheckpointTranscriptMaterializationError,
    materialize_checkpoint_transcript_in_uow,
)
from .checkpoint_transcript_writer import write_transcript_representation_in_uow
from .serialization import to_json_safe


class WaitingCheckpointConflictError(RuntimeError):
    """Normalized WAITING safe point cannot be proven against durable state."""


async def validate_checkpoint_parent_lineage_in_uow(
    uow,
    *,
    execution,
    parent_checkpoint_id: str | None,
    child_checkpoint_id: str | None = None,
    child_execution_revision: int | None = None,
):
    """Fail closed unless a semantic checkpoint parent is same-lineage."""

    if parent_checkpoint_id is None:
        return None
    parent_id = str(parent_checkpoint_id)
    if child_checkpoint_id is not None and parent_id == str(child_checkpoint_id):
        raise WaitingCheckpointConflictError(
            "Checkpoint parent_checkpoint_id may not self-reference."
        )

    parent = await uow.agents.get_execution_checkpoint(parent_id)
    if parent is None:
        raise WaitingCheckpointConflictError(
            f"Unknown parent checkpoint: {parent_id}"
        )
    if (
        parent.execution_id != execution.id
        or parent.session_id != execution.session_id
        or parent.task_id != execution.task_id
        or parent.branch_id != execution.branch_id
    ):
        raise WaitingCheckpointConflictError(
            "Checkpoint parent belongs to another execution/task/branch lineage."
        )
    if (
        child_execution_revision is not None
        and int(parent.execution_revision) >= int(child_execution_revision)
    ):
        raise WaitingCheckpointConflictError(
            "Checkpoint parent revision must precede the child checkpoint."
        )
    return parent


def _pending_identity(items: Sequence[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            int(item["ordinal"]),
            str(item["invocation_id"]),
            str(item["tool_call_id"]),
            str(item["capability_id"]),
        )
        for item in sorted(items, key=lambda value: int(value["ordinal"]))
    )


async def verify_committed_waiting_checkpoint(
    uow,
    *,
    execution,
    source_revision: int,
    checkpoint_values: dict[str, Any],
    pending_invocations: Sequence[dict[str, Any]],
) -> None:
    checkpoint_id = str(checkpoint_values["checkpoint_id"])
    expected_revision = source_revision + 1
    checkpoint = await uow.agents.get_execution_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise WaitingCheckpointConflictError(
            f"Missing committed checkpoint: {checkpoint_id}"
        )
    if (
        checkpoint.execution_id != execution.id
        or checkpoint.execution_revision != expected_revision
        or checkpoint.session_id != execution.session_id
        or checkpoint.task_id != execution.task_id
        or checkpoint.branch_id != execution.branch_id
    ):
        raise WaitingCheckpointConflictError(
            "Committed checkpoint does not match execution "
            "task/branch/session transition lineage."
        )

    expected_snapshot = checkpoint_values.get("transcript_snapshot")
    if expected_snapshot is None:
        raise WaitingCheckpointConflictError(
            "Idempotent WAITING replay requires the expected transcript snapshot."
        )
    try:
        committed_messages = await materialize_checkpoint_transcript_in_uow(
            uow,
            checkpoint,
        )
    except CheckpointTranscriptMaterializationError as exc:
        raise WaitingCheckpointConflictError(str(exc)) from exc
    try:
        expected_messages = canonical_transcript_messages(
            list(expected_snapshot)
        )
        committed_canonical = canonical_transcript_messages(
            [item.model_dump(mode="json") for item in committed_messages]
        )
    except Exception as exc:
        raise WaitingCheckpointConflictError(
            "Idempotent WAITING replay transcript is not canonicalizable."
        ) from exc
    if committed_canonical != expected_messages:
        raise WaitingCheckpointConflictError(
            "Committed checkpoint transcript differs from idempotent replay."
        )

    persisted = await uow.agents.list_checkpoint_pending_invocations(
        checkpoint_id
    )
    persisted_identity = tuple(
        (
            item.ordinal,
            item.invocation_id,
            item.tool_call_id,
            item.capability_id,
        )
        for item in persisted
    )
    if persisted_identity != _pending_identity(pending_invocations):
        raise WaitingCheckpointConflictError(
            "Committed checkpoint pending invocation identity differs."
        )

    if execution.revision == expected_revision:
        if str(execution.state) != "WAITING":
            raise WaitingCheckpointConflictError(
                "Checkpoint target revision exists but execution is not WAITING."
            )
        if execution.current_checkpoint_id != checkpoint_id:
            raise WaitingCheckpointConflictError(
                "WAITING execution does not point at committed checkpoint."
            )


async def stage_waiting_checkpoint(
    uow,
    *,
    execution,
    source_revision: int,
    transition_values: dict[str, Any],
    checkpoint_values: dict[str, Any],
    pending_invocations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Stage checkpoint + R6 snapshots before the AgentExecution CAS.

    The caller owns the surrounding transaction and MUST roll back when the
    subsequent execution CAS or TaskBudget mutation loses its race.
    """
    target_revision = source_revision + 1
    if execution.revision != source_revision or str(execution.state) != "RUNNING":
        raise WaitingCheckpointConflictError(
            f"Execution {execution.id} is not RUNNING@{source_revision}."
        )

    checkpoint = dict(checkpoint_values)
    checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
    if not checkpoint_id:
        raise ValueError("checkpoint_id must be non-empty")
    if checkpoint.get("execution_id") != execution.id:
        raise WaitingCheckpointConflictError(
            "Checkpoint execution_id does not match AgentExecution."
        )
    if int(checkpoint.get("execution_revision", -1)) != target_revision:
        raise WaitingCheckpointConflictError(
            "Checkpoint revision must equal target AgentExecution revision."
        )
    if checkpoint.get("session_id") != execution.session_id:
        raise WaitingCheckpointConflictError(
            "Checkpoint session_id does not match AgentExecution."
        )
    if checkpoint.get("task_id") != execution.task_id:
        raise WaitingCheckpointConflictError(
            "Checkpoint task_id does not match AgentExecution."
        )
    if checkpoint.get("branch_id") != execution.branch_id:
        raise WaitingCheckpointConflictError(
            "Checkpoint branch_id does not match AgentExecution."
        )
    if execution.task_id is not None and execution.branch_id is None:
        raise WaitingCheckpointConflictError(
            "Task-scoped AgentExecution requires normalized branch_id "
            "before WAITING checkpoint creation."
        )

    wait_reason = str(transition_values.get("wait_reason") or "")
    if not wait_reason or checkpoint.get("wait_reason") != wait_reason:
        raise WaitingCheckpointConflictError(
            "Checkpoint wait_reason must match WAITING transition."
        )

    checkpoint.setdefault(
        "parent_checkpoint_id",
        getattr(execution, "current_checkpoint_id", None),
    )
    expected_parent_checkpoint_id = getattr(
        execution,
        "current_checkpoint_id",
        None,
    )
    if checkpoint.get("parent_checkpoint_id") != expected_parent_checkpoint_id:
        raise WaitingCheckpointConflictError(
            "Checkpoint parent_checkpoint_id must match the execution current checkpoint."
        )
    await validate_checkpoint_parent_lineage_in_uow(
        uow,
        execution=execution,
        parent_checkpoint_id=checkpoint.get("parent_checkpoint_id"),
        child_checkpoint_id=checkpoint_id,
        child_execution_revision=target_revision,
    )

    snapshot = checkpoint.get("transcript_snapshot")
    if snapshot is not None:
        checkpoint["transcript_snapshot"] = to_json_safe(
            list(snapshot),
            path="agent_execution_checkpoints.transcript_snapshot",
        )
    elif "transcript_snapshot" in checkpoint:
        checkpoint["transcript_snapshot"] = None

    transcript_ref = checkpoint.get("transcript_ref")
    transcript_version = checkpoint.get("transcript_version")
    if transcript_ref is not None or transcript_version is not None:
        raise WaitingCheckpointConflictError(
            "Checkpoint ref-bearing authority is owned by the R11-D "
            "representation writer and may not be caller supplied."
        )
    if checkpoint.get("transcript_snapshot") is None:
        raise WaitingCheckpointConflictError(
            "R11-D REF_BACKED cutover requires an inline transcript proof snapshot."
        )

    proven = await write_transcript_representation_in_uow(
        uow,
        messages=checkpoint["transcript_snapshot"],
        candidate_parent_checkpoint_id=checkpoint.get("parent_checkpoint_id"),
    )
    checkpoint["transcript_ref"] = proven.transcript_ref
    checkpoint["transcript_version"] = proven.transcript_version
    # R11-D5 cutover: caller-provided inline transcript remains proof input,
    # but new durable checkpoint authority is REF_BACKED-only.
    checkpoint["transcript_snapshot"] = None

    checkpoint["metadata_json"] = to_json_safe(
        checkpoint.get("metadata_json") or {},
        path="agent_execution_checkpoints.metadata",
    )

    refs = sorted(
        (dict(item) for item in pending_invocations),
        key=lambda item: int(item["ordinal"]),
    )
    seen_ordinals: set[int] = set()
    seen_invocations: set[str] = set()
    seen_tool_calls: set[str] = set()
    frozen_rows: list[dict[str, Any]] = []
    origin_clients: set[str] = set()

    for ref in refs:
        ordinal = int(ref["ordinal"])
        invocation_id = str(ref["invocation_id"])
        tool_call_id = str(ref["tool_call_id"])
        capability_id = str(ref["capability_id"])
        if ordinal < 0 or ordinal in seen_ordinals:
            raise WaitingCheckpointConflictError(
                "Pending invocation ordinals must be unique and non-negative."
            )
        if invocation_id in seen_invocations or tool_call_id in seen_tool_calls:
            raise WaitingCheckpointConflictError(
                "Pending invocation/tool-call identity must be unique."
            )
        seen_ordinals.add(ordinal)
        seen_invocations.add(invocation_id)
        seen_tool_calls.add(tool_call_id)

        repository = getattr(uow, "capability_invocations", None)
        if repository is None:
            raise WaitingCheckpointConflictError(
                "Shared UoW has no capability invocation repository."
            )
        invocation = await repository.get_record(invocation_id)
        if invocation is None:
            raise WaitingCheckpointConflictError(
                f"Unknown CapabilityInvocation: {invocation_id}"
            )
        if invocation.execution_id != execution.id:
            raise WaitingCheckpointConflictError(
                "CapabilityInvocation belongs to another execution."
            )
        if invocation.tool_call_id != tool_call_id:
            raise WaitingCheckpointConflictError(
                "CapabilityInvocation tool_call_id changed before checkpoint."
            )
        if invocation.capability_id != capability_id:
            raise WaitingCheckpointConflictError(
                "CapabilityInvocation capability_id changed before checkpoint."
            )
        if invocation.origin_client_id:
            origin_clients.add(str(invocation.origin_client_id))

        frozen_rows.append(
            {
                "checkpoint_id": checkpoint_id,
                "ordinal": ordinal,
                "invocation_id": invocation.invocation_id,
                "invocation_revision": invocation.revision,
                "tool_call_id": invocation.tool_call_id,
                "capability_id": invocation.capability_id,
                "capability_version": invocation.capability_version,
                "request_fingerprint": invocation.request_fingerprint,
                "idempotency": invocation.idempotency or "UNKNOWN",
                "observed_remote_outcome_state": invocation.remote_outcome_state,
                "origin_client_id": invocation.origin_client_id,
                "origin_connection_id": invocation.connection_id,
            }
        )

    if len(origin_clients) > 1:
        raise WaitingCheckpointConflictError(
            "R7 initial continuation does not support multi-client fan-in."
        )
    checkpoint_origin_client = checkpoint.get("origin_client_id")
    if (
        checkpoint_origin_client
        and origin_clients
        and checkpoint_origin_client not in origin_clients
    ):
        raise WaitingCheckpointConflictError(
            "Checkpoint origin_client_id differs from pending invocation owner."
        )

    await uow.agents.save_execution_checkpoint(checkpoint)
    await uow.agents.save_checkpoint_pending_invocations(frozen_rows)

    execution_values = dict(transition_values)
    execution_values["current_checkpoint_id"] = checkpoint_id
    if wait_reason == "CONNECTION":
        execution_values["bound_connection_id"] = None
        if checkpoint_origin_client:
            execution_values["bound_client_id"] = checkpoint_origin_client
    return execution_values
