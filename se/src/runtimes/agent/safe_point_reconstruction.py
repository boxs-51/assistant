from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ...infrastructure.storage.transcript_representation import (
    canonical_transcript_messages,
)
from .checkpoint_transcript import (
    CheckpointTranscriptMaterializationError,
    materialize_checkpoint_transcript_in_uow,
)


class SafePointReconstructionError(RuntimeError):
    """Durable state cannot prove one R7-C-compatible safe point."""


@dataclass(frozen=True, slots=True)
class R7CSafePoint:
    transcript_snapshot: tuple[dict[str, Any], ...]
    iteration_number: int
    iteration_id: str | None
    ordered_tool_calls: tuple[Any, ...]
    ordered_pending_invocations: tuple[dict[str, Any], ...]
    checkpoint_id: str | None
    checkpoint_revision: int | None


def recovery_receipt_payload(
    *,
    execution_id: str,
    source_revision: int,
    target_revision: int,
    checkpoint_id: str,
    observed_owner_instance_id: str,
    observed_lease_generation: int,
    observed_lease_expires_at: datetime,
    takeover_now_utc: datetime,
) -> dict[str, Any]:
    """Return the deterministic R12-E receipt/target proof payload."""

    return {
        "execution_id": str(execution_id),
        "source_revision": int(source_revision),
        "target_revision": int(target_revision),
        "target_state": "WAITING",
        "wait_reason": "RECOVERY",
        "checkpoint_id": str(checkpoint_id),
        "observed_owner_instance_id": str(observed_owner_instance_id),
        "observed_lease_generation": int(observed_lease_generation),
        "observed_lease_expires_at": observed_lease_expires_at.isoformat(),
        "takeover_now_utc": takeover_now_utc.isoformat(),
    }


def recovery_safe_point_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_message_dict(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return dict(item.model_dump(mode="json"))
    if isinstance(item, dict):
        return dict(item)
    raise SafePointReconstructionError(
        "SAFE_POINT_TRANSCRIPT_CORRUPT: transcript item is not a message mapping."
    )


def _committed_tool_message(result) -> dict[str, Any]:
    return {
        "role": "tool",
        "content": (
            result.output
            if result.success
            else {
                "error_code": result.error_code,
                "error_message": result.error_message,
            }
        ),
        "tool_calls": [],
        "name": result.capability_id,
        "tool_call_id": result.tool_call_id,
        "metadata": {
            "success": result.success,
            "retryable": result.retryable,
        },
    }


async def _sanitize_transcript_in_uow(
    uow,
    *,
    execution_id: str,
    raw_messages,
    active_tool_call_ids: frozenset[str],
) -> tuple[dict[str, Any], ...]:
    sanitized: list[dict[str, Any]] = []
    for raw in list(raw_messages or ()):
        message = _as_message_dict(raw)
        if message.get("role") != "tool":
            sanitized.append(message)
            continue

        tool_call_id = str(message.get("tool_call_id") or "")
        if not tool_call_id:
            continue
        if tool_call_id in active_tool_call_ids:
            # R7-C rematerializes the active batch at most once after resume.
            continue

        result = await uow.agents.get_tool_result(
            execution_id,
            tool_call_id,
        )
        if (
            result is None
            or getattr(result, "commit_state", "PROVISIONAL") != "COMMITTED"
        ):
            continue
        if (
            str(result.execution_id) != str(execution_id)
            or str(result.tool_call_id) != tool_call_id
        ):
            raise SafePointReconstructionError(
                "SAFE_POINT_TOOL_RESULT_CONFLICT: committed tool result "
                "does not match transcript execution/tool identity."
            )
        sanitized.append(_committed_tool_message(result))

    try:
        # Canonicalization is a validation/storage-proof boundary only.  Keep
        # the pre-existing R7-C outward resume projection shape for ordinary
        # non-tool mappings instead of leaking canonical default fields.
        canonical_transcript_messages(sanitized)
    except Exception as exc:
        raise SafePointReconstructionError(
            "SAFE_POINT_TRANSCRIPT_CORRUPT: transcript is not canonicalizable."
        ) from exc
    return tuple(dict(item) for item in sanitized)


async def reconstruct_r7c_safe_point_in_uow(
    uow,
    execution,
    *,
    require_pending_invocation_authority: bool = False,
) -> R7CSafePoint:
    """Reconstruct the canonical R7-C safe prefix inside a caller-owned UoW.

    This helper is intentionally read-only.  It never commits, rolls back,
    reconciles, dispatches, activates runtime work, or mutates an invocation.
    """

    execution_id = str(getattr(execution, "id", "") or "")
    if not execution_id:
        raise SafePointReconstructionError(
            "SAFE_POINT_EXECUTION_INVALID: execution id is empty."
        )

    iterations = list(await uow.agents.list_iterations(execution_id))
    iteration_by_number: dict[int, Any] = {}
    for item in iterations:
        if str(item.execution_id) != execution_id:
            raise SafePointReconstructionError(
                "SAFE_POINT_ITERATION_LINEAGE_CONFLICT: iteration belongs "
                "to another execution."
            )
        number = int(item.iteration)
        if number in iteration_by_number:
            raise SafePointReconstructionError(
                "SAFE_POINT_ITERATION_AMBIGUOUS: duplicate durable iteration number."
            )
        iteration_by_number[number] = item

    latest_iteration = (
        iteration_by_number[max(iteration_by_number)]
        if iteration_by_number
        else None
    )

    checkpoint = None
    checkpoint_messages: tuple[dict[str, Any], ...] = ()
    checkpoint_id = getattr(execution, "current_checkpoint_id", None)
    if checkpoint_id:
        checkpoint = await uow.agents.get_execution_checkpoint(
            str(checkpoint_id)
        )
        if checkpoint is None:
            raise SafePointReconstructionError(
                "SAFE_POINT_CHECKPOINT_MISSING: current checkpoint does not exist."
            )
        if (
            str(checkpoint.execution_id) != execution_id
            or checkpoint.session_id != execution.session_id
            or checkpoint.task_id != execution.task_id
            or checkpoint.branch_id != execution.branch_id
            or int(checkpoint.execution_revision) > int(execution.revision)
        ):
            raise SafePointReconstructionError(
                "SAFE_POINT_CHECKPOINT_LINEAGE_CONFLICT: current checkpoint "
                "does not match the execution lineage/revision."
            )
        checkpoint_iteration = iteration_by_number.get(
            int(checkpoint.iteration)
        )
        if checkpoint_iteration is None:
            raise SafePointReconstructionError(
                "SAFE_POINT_CHECKPOINT_ITERATION_MISSING: checkpoint "
                "iteration is absent from durable history."
            )
        try:
            materialized = await materialize_checkpoint_transcript_in_uow(
                uow,
                checkpoint,
            )
        except CheckpointTranscriptMaterializationError as exc:
            raise SafePointReconstructionError(str(exc)) from exc
        checkpoint_messages = tuple(
            _as_message_dict(item) for item in materialized
        )
    else:
        checkpoint_iteration = None

    # If the checkpoint is the current semantic revision, it pins the same
    # active batch used by ordinary R7-C resume.  A stale RUNNING execution can
    # legitimately be one or more semantic revisions beyond its prior WAITING
    # checkpoint; in that case the highest durable iteration is the current
    # batch authority while the checkpoint remains the immutable safe prefix.
    if (
        checkpoint is not None
        and int(checkpoint.execution_revision) == int(execution.revision)
    ):
        active_iteration = checkpoint_iteration
    else:
        active_iteration = latest_iteration

    ordered_tool_calls: list[Any] = []
    ordered_pending: list[dict[str, Any]] = []
    active_ids: tuple[str, ...] = ()
    if active_iteration is not None:
        active_ids = tuple(
            str(item) for item in (active_iteration.tool_call_ids or ())
        )
        if any(not item for item in active_ids) or len(set(active_ids)) != len(active_ids):
            raise SafePointReconstructionError(
                "SAFE_POINT_ACTIVE_BATCH_AMBIGUOUS: tool_call_ids must be "
                "non-empty and unique."
            )

        calls = list(
            await uow.agents.list_tool_calls(
                execution_id,
                active_iteration.id,
            )
        )
        by_id: dict[str, Any] = {}
        for call in calls:
            call_id = str(call.tool_call_id)
            if call_id in by_id:
                raise SafePointReconstructionError(
                    "SAFE_POINT_TOOL_CALL_AMBIGUOUS: duplicate durable tool binding."
                )
            by_id[call_id] = call

        invocation_repository = getattr(
            uow,
            "capability_invocations",
            None,
        )
        for ordinal, tool_call_id in enumerate(active_ids):
            call = by_id.get(tool_call_id)
            if call is None:
                raise SafePointReconstructionError(
                    "SAFE_POINT_TOOL_CALL_MISSING: active batch references "
                    f"missing tool call {tool_call_id!r}."
                )
            if (
                str(call.execution_id) != execution_id
                or str(call.iteration_id) != str(active_iteration.id)
                or str(call.tool_call_id) != tool_call_id
                or not str(call.invocation_id or "")
                or not str(call.capability_id or "")
            ):
                raise SafePointReconstructionError(
                    "SAFE_POINT_TOOL_CALL_CONFLICT: active tool binding "
                    "does not match execution/iteration identity."
                )
            ordered_tool_calls.append(call)

            result = await uow.agents.get_tool_result(
                execution_id,
                tool_call_id,
            )
            if result is not None and getattr(
                result,
                "commit_state",
                "PROVISIONAL",
            ) == "COMMITTED":
                if (
                    str(result.execution_id) != execution_id
                    or str(result.tool_call_id) != tool_call_id
                    or str(result.invocation_id) != str(call.invocation_id)
                    or str(result.capability_id) != str(call.capability_id)
                ):
                    raise SafePointReconstructionError(
                        "SAFE_POINT_COMMITTED_RESULT_CONFLICT: committed "
                        "result identity differs from active tool binding."
                    )
                continue

            if invocation_repository is None:
                if require_pending_invocation_authority:
                    raise SafePointReconstructionError(
                        "SAFE_POINT_INVOCATION_REPOSITORY_MISSING: shared UoW "
                        "cannot prove unresolved invocation authority."
                    )
                continue
            invocation = await invocation_repository.get_record(
                str(call.invocation_id)
            )
            if invocation is None:
                if require_pending_invocation_authority:
                    raise SafePointReconstructionError(
                        "SAFE_POINT_INVOCATION_MISSING: unresolved active slot "
                        "has no durable CapabilityInvocation."
                    )
                # Preserve ordinary R7-C/local pre-dispatch resume behavior.
                # R12-E passes require_pending_invocation_authority=True and
                # therefore never accepts this compatibility branch.
                continue
            if (
                str(invocation.execution_id) != execution_id
                or str(invocation.tool_call_id) != tool_call_id
                or str(invocation.capability_id) != str(call.capability_id)
            ):
                raise SafePointReconstructionError(
                    "SAFE_POINT_INVOCATION_CONFLICT: invocation identity "
                    "differs from active tool binding."
                )
            ordered_pending.append(
                {
                    "ordinal": ordinal,
                    "invocation_id": str(invocation.invocation_id),
                    "tool_call_id": tool_call_id,
                    "capability_id": str(invocation.capability_id),
                }
            )

    active_set = frozenset(active_ids)

    if checkpoint is not None and (
        int(checkpoint.execution_revision) == int(execution.revision)
    ):
        raw_source = checkpoint_messages
    else:
        raw_source = (
            getattr(execution, "transcript", None)
            or (
                getattr(latest_iteration, "transcript", None)
                if latest_iteration is not None
                else None
            )
            or checkpoint_messages
        )

    safe_transcript = await _sanitize_transcript_in_uow(
        uow,
        execution_id=execution_id,
        raw_messages=raw_source,
        active_tool_call_ids=active_set,
    )

    if checkpoint is not None and (
        int(checkpoint.execution_revision) < int(execution.revision)
    ):
        safe_checkpoint_prefix = await _sanitize_transcript_in_uow(
            uow,
            execution_id=execution_id,
            raw_messages=checkpoint_messages,
            active_tool_call_ids=active_set,
        )
        try:
            canonical_safe_transcript = tuple(
                dict(item)
                for item in canonical_transcript_messages(
                    list(safe_transcript)
                )
            )
            canonical_checkpoint_prefix = tuple(
                dict(item)
                for item in canonical_transcript_messages(
                    list(safe_checkpoint_prefix)
                )
            )
        except Exception as exc:
            raise SafePointReconstructionError(
                "SAFE_POINT_TRANSCRIPT_CORRUPT: transcript prefix proof is "
                "not canonicalizable."
            ) from exc
        if (
            len(canonical_safe_transcript)
            < len(canonical_checkpoint_prefix)
            or canonical_safe_transcript[
                : len(canonical_checkpoint_prefix)
            ]
            != canonical_checkpoint_prefix
        ):
            raise SafePointReconstructionError(
                "SAFE_POINT_TRANSCRIPT_DIVERGENCE: durable RUNNING transcript "
                "does not extend the current checkpoint prefix."
            )

    return R7CSafePoint(
        transcript_snapshot=safe_transcript,
        iteration_number=(
            int(active_iteration.iteration)
            if active_iteration is not None
            else 0
        ),
        iteration_id=(
            str(active_iteration.id)
            if active_iteration is not None
            else None
        ),
        ordered_tool_calls=tuple(ordered_tool_calls),
        ordered_pending_invocations=tuple(ordered_pending),
        checkpoint_id=(
            str(checkpoint.checkpoint_id)
            if checkpoint is not None
            else None
        ),
        checkpoint_revision=(
            int(checkpoint.execution_revision)
            if checkpoint is not None
            else None
        ),
    )


__all__ = [
    "R7CSafePoint",
    "SafePointReconstructionError",
    "reconstruct_r7c_safe_point_in_uow",
    "recovery_receipt_payload",
    "recovery_safe_point_fingerprint",
]
