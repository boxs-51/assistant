from __future__ import annotations

from typing import Any

from ...infrastructure.storage.transcript_representation import (
    canonical_transcript_messages,
)
from .contracts.inference import InferenceMessage


class CheckpointTranscriptMaterializationError(RuntimeError):
    """R11-C canonical checkpoint representation failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


CHECKPOINT_REPRESENTATION_ERROR_CODES = frozenset(
    {
        "INVALID_CHECKPOINT_REPRESENTATION_STATE",
        "MISSING_TRANSCRIPT_REPRESENTATION",
        "TRANSCRIPT_REPRESENTATION_VERSION_MISMATCH",
        "TRANSCRIPT_REPRESENTATION_ANCESTRY_INVALID",
        "TRANSCRIPT_REPRESENTATION_DEPTH_EXCEEDED",
        "TRANSCRIPT_REPRESENTATION_CORRUPT",
        "DUAL_TRANSCRIPT_MISMATCH",
    }
)


def checkpoint_representation_error_code(value: object) -> str | None:
    code = str(value or "").split(":", 1)[0]
    return code if code in CHECKPOINT_REPRESENTATION_ERROR_CODES else None


def _representation_error_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "ancestry" in message or "cycle" in message or "parent" in message:
        return "TRANSCRIPT_REPRESENTATION_ANCESTRY_INVALID"
    if "depth" in message:
        return "TRANSCRIPT_REPRESENTATION_DEPTH_EXCEEDED"
    return "TRANSCRIPT_REPRESENTATION_CORRUPT"


async def materialize_checkpoint_transcript_in_uow(
    uow,
    checkpoint,
) -> tuple[InferenceMessage, ...]:
    """Materialize one exact checkpoint transcript without mutable fallback."""

    snapshot = getattr(checkpoint, "transcript_snapshot", None)
    transcript_ref = getattr(checkpoint, "transcript_ref", None)
    transcript_version = getattr(checkpoint, "transcript_version", None)

    has_inline = snapshot is not None
    has_ref = transcript_ref is not None
    has_version = transcript_version is not None

    if has_ref != has_version or not (has_inline or has_ref):
        raise CheckpointTranscriptMaterializationError(
            "INVALID_CHECKPOINT_REPRESENTATION_STATE",
            "Checkpoint transcript representation fields form an illegal state.",
        )

    inline_messages: list[dict[str, Any]] | None = None
    if has_inline:
        try:
            inline_messages = canonical_transcript_messages(list(snapshot))
        except Exception as exc:
            raise CheckpointTranscriptMaterializationError(
                "TRANSCRIPT_REPRESENTATION_CORRUPT",
                "Inline checkpoint transcript is not canonicalizable.",
            ) from exc

    ref_messages: list[dict[str, Any]] | None = None
    if has_ref:
        ref = str(transcript_ref)
        version = int(transcript_version)
        record = await uow.agents.get_transcript_representation(ref, version)
        if record is None:
            versions = await uow.agents.list_transcript_representation_versions(ref)
            if versions:
                raise CheckpointTranscriptMaterializationError(
                    "TRANSCRIPT_REPRESENTATION_VERSION_MISMATCH",
                    "Checkpoint transcript_ref exists at different version(s).",
                )
            raise CheckpointTranscriptMaterializationError(
                "MISSING_TRANSCRIPT_REPRESENTATION",
                "Checkpoint transcript representation does not exist.",
            )
        try:
            ref_messages = await uow.agents.materialize_transcript_representation(
                ref,
                version,
            )
            ref_messages = canonical_transcript_messages(ref_messages)
        except CheckpointTranscriptMaterializationError:
            raise
        except Exception as exc:
            raise CheckpointTranscriptMaterializationError(
                _representation_error_code(exc),
                "Checkpoint transcript representation failed integrity validation.",
            ) from exc

    if inline_messages is not None and ref_messages is not None:
        if inline_messages != ref_messages:
            raise CheckpointTranscriptMaterializationError(
                "DUAL_TRANSCRIPT_MISMATCH",
                "Inline and ref-backed checkpoint transcripts differ canonically.",
            )
        logical = inline_messages
    else:
        logical = inline_messages if inline_messages is not None else ref_messages

    if logical is None:
        raise CheckpointTranscriptMaterializationError(
            "INVALID_CHECKPOINT_REPRESENTATION_STATE",
            "Checkpoint transcript has no legal logical authority.",
        )

    try:
        return tuple(InferenceMessage.model_validate(item) for item in logical)
    except Exception as exc:
        raise CheckpointTranscriptMaterializationError(
            "TRANSCRIPT_REPRESENTATION_CORRUPT",
            "Materialized checkpoint transcript is invalid.",
        ) from exc
