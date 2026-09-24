from __future__ import annotations

from dataclasses import dataclass

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
) -> CheckpointBackfillResult:
    """Converge historical LEGACY_INLINE checkpoints to DUAL in one caller UoW.

    Checkpoint parent lineage is only a candidate for representation ancestry.
    A child waits while an extant candidate parent is still LEGACY_INLINE, so
    worker ordering cannot choose a different physical ancestry. Missing
    parents do not prove ancestry and therefore fall back through the D1 FULL
    path. Existing DUAL/REF_BACKED rows are never rewritten by this scan.
    """

    repo = uow.agents
    candidates = await repo.list_checkpoint_backfill_candidates(limit=limit)
    pending = {}
    converted = 0
    converged = 0

    for checkpoint in candidates:
        snapshot = checkpoint.transcript_snapshot
        transcript_ref = checkpoint.transcript_ref
        transcript_version = checkpoint.transcript_version
        if (transcript_ref is None) != (transcript_version is None):
            raise CheckpointBackfillError(
                "Checkpoint has a partial transcript representation pair."
            )
        if snapshot is None and transcript_ref is None:
            raise CheckpointBackfillError(
                "Checkpoint transcript is not reconstructable."
            )
        if transcript_ref is not None:
            await _canonical_checkpoint_messages(uow, checkpoint)
            converged += 1
            continue
        pending[str(checkpoint.checkpoint_id)] = checkpoint

    while pending:
        progressed = False
        for checkpoint_id, checkpoint in tuple(pending.items()):
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
                        # The parent exists but remains LEGACY_INLINE. Defer the
                        # child even when the parent is outside this bounded
                        # batch; a later pass can converge after the parent.
                        continue
                    await _canonical_checkpoint_messages(uow, parent)

            expected = await _canonical_checkpoint_messages(uow, checkpoint)
            proven = await write_transcript_representation_in_uow(
                uow,
                messages=expected,
                candidate_parent_checkpoint_id=(
                    str(parent_id) if parent_id is not None else None
                ),
            )
            bound = (
                await repo.bind_checkpoint_transcript_representation_if_legacy(
                    checkpoint_id,
                    transcript_ref=proven.transcript_ref,
                    transcript_version=proven.transcript_version,
                )
            )
            if bound is None:
                winner = await repo.get_execution_checkpoint(checkpoint_id)
                if winner is None:
                    raise CheckpointBackfillError(
                        "Checkpoint disappeared during backfill."
                    )
                winner_messages = await _canonical_checkpoint_messages(
                    uow,
                    winner,
                )
                if winner_messages != expected:
                    raise CheckpointBackfillError(
                        "Concurrent checkpoint backfill converged to different "
                        "logical transcript authority."
                    )
                converged += 1
            else:
                bound_messages = await _canonical_checkpoint_messages(
                    uow,
                    bound,
                )
                if bound_messages != expected:
                    raise CheckpointBackfillError(
                        "Backfilled DUAL checkpoint does not match historical "
                        "inline transcript."
                    )
                converted += 1

            pending.pop(checkpoint_id)
            progressed = True

        if not progressed:
            break

    return CheckpointBackfillResult(
        scanned=len(candidates),
        converted=converted,
        converged=converged,
        deferred=len(pending),
    )
