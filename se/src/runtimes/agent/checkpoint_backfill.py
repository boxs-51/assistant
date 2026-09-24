from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ...infrastructure.storage.transcript_representation import (
    canonical_transcript_messages,
)
from .checkpoint_transcript import (
    CheckpointTranscriptMaterializationError,
    materialize_checkpoint_transcript_in_uow,
)
from .checkpoint_transcript_writer import write_transcript_representation_in_uow


class CheckpointBackfillError(RuntimeError):
    """Historical checkpoint cannot be converged safely."""


@dataclass(frozen=True)
class CheckpointBackfillResult:
    scanned: int
    converted: int
    converged: int
    deferred: int
    next_conversion_after: tuple[datetime, str] | None = None
    next_validation_after: tuple[datetime, str] | None = None


def _as_json_messages(messages):
    return [item.model_dump(mode="json") for item in messages]


async def _canonical_checkpoint_messages(uow, checkpoint):
    try:
        materialized = await materialize_checkpoint_transcript_in_uow(
            uow,
            checkpoint,
        )
    except CheckpointTranscriptMaterializationError as exc:
        raise CheckpointBackfillError(str(exc)) from exc
    return canonical_transcript_messages(_as_json_messages(materialized))


async def backfill_legacy_inline_checkpoints_in_uow(
    uow,
    *,
    limit: int | None = None,
    scan_limit: int | None = None,
    conversion_after: tuple[datetime, str] | None = None,
    validation_after: tuple[datetime, str] | None = None,
) -> CheckpointBackfillResult:
    """Converge LEGACY_INLINE to DUAL with independent ref validation progress.

    The conversion budget applies only to rows that still require conversion.
    Ref-bearing DUAL/REF_BACKED verification uses a separate keyset cursor so
    already-converged rows cannot starve later LEGACY_INLINE work.
    """

    repo = uow.agents
    if scan_limit is None:
        scan_limit = limit
    if scan_limit is not None and scan_limit <= 0:
        raise ValueError("scan_limit must be positive")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")

    after_created_at = None
    after_checkpoint_id = None
    if conversion_after is not None:
        after_created_at, after_checkpoint_id = conversion_after

    legacy_page = await repo.list_legacy_inline_checkpoint_backfill_candidates(
        limit=scan_limit,
        after_created_at=after_created_at,
        after_checkpoint_id=after_checkpoint_id,
    )
    scanned = 0
    converted = 0
    converged = 0
    deferred = 0
    next_conversion_after = conversion_after

    for checkpoint in legacy_page:
        scanned += 1
        next_conversion_after = (
            checkpoint.created_at,
            str(checkpoint.checkpoint_id),
        )
        parent_id = checkpoint.parent_checkpoint_id
        if parent_id is not None:
            parent = await repo.get_execution_checkpoint(str(parent_id))
            if parent is not None:
                parent_ref = parent.transcript_ref
                parent_version = parent.transcript_version
                if (parent_ref is None) != (parent_version is None):
                    raise CheckpointBackfillError(
                        "Parent checkpoint has a partial transcript pair."
                    )
                if parent_ref is None:
                    if parent.transcript_snapshot is None:
                        raise CheckpointBackfillError(
                            "Parent checkpoint transcript is not reconstructable."
                        )
                    deferred += 1
                    continue
                await _canonical_checkpoint_messages(uow, parent)

        if limit is not None and converted >= limit:
            continue

        expected = await _canonical_checkpoint_messages(uow, checkpoint)
        proven = await write_transcript_representation_in_uow(
            uow,
            messages=expected,
            candidate_parent_checkpoint_id=(
                str(parent_id) if parent_id is not None else None
            ),
        )
        bound = await repo.bind_checkpoint_transcript_representation_if_legacy(
            str(checkpoint.checkpoint_id),
            transcript_ref=proven.transcript_ref,
            transcript_version=proven.transcript_version,
        )
        if bound is None:
            winner = await repo.get_execution_checkpoint(
                str(checkpoint.checkpoint_id)
            )
            if winner is None:
                raise CheckpointBackfillError(
                    "Checkpoint disappeared during backfill."
                )
            winner_messages = await _canonical_checkpoint_messages(uow, winner)
            if winner_messages != expected:
                raise CheckpointBackfillError(
                    "Concurrent checkpoint backfill converged to different "
                    "logical transcript authority."
                )
            converged += 1
        else:
            bound_messages = await _canonical_checkpoint_messages(uow, bound)
            if bound_messages != expected:
                raise CheckpointBackfillError(
                    "Backfilled DUAL checkpoint does not match historical "
                    "inline transcript."
                )
            converted += 1

    if scan_limit is None or len(legacy_page) < scan_limit:
        next_conversion_after = None

    has_legacy = await repo.has_legacy_inline_checkpoints()

    # One public call executes at most one bounded phase. If this invocation
    # examined any conversion page, return immediately even when it converted
    # the final LEGACY row. Validation starts on the next call, when the call
    # begins with no LEGACY work globally.
    if legacy_page or has_legacy:
        if not has_legacy:
            next_conversion_after = None
        return CheckpointBackfillResult(
            scanned=scanned,
            converted=converted,
            converged=converged,
            deferred=deferred,
            next_conversion_after=next_conversion_after,
            next_validation_after=validation_after,
        )

    # Separate ref-bearing verification phase with deterministic keyset
    # progress. This phase never mutates DUAL/REF_BACKED checkpoints.
    after_created_at = None
    after_checkpoint_id = None
    if validation_after is not None:
        after_created_at, after_checkpoint_id = validation_after

    validation_rows = (
        await repo.list_ref_backed_checkpoint_validation_candidates(
            limit=scan_limit,
            after_created_at=after_created_at,
            after_checkpoint_id=after_checkpoint_id,
        )
    )
    next_validation_after = validation_after
    for checkpoint in validation_rows:
        scanned += 1
        await _canonical_checkpoint_messages(uow, checkpoint)
        converged += 1
        next_validation_after = (
            checkpoint.created_at,
            str(checkpoint.checkpoint_id),
        )

    if scan_limit is None or len(validation_rows) < scan_limit:
        next_validation_after = None

    return CheckpointBackfillResult(
        scanned=scanned,
        converted=converted,
        converged=converged,
        deferred=deferred,
        next_conversion_after=next_conversion_after,
        next_validation_after=next_validation_after,
    )
