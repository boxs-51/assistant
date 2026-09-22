from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Iterable, Mapping


class LegacyCheckpointMaterializationError(RuntimeError):
    """Fail-closed legacy Phase 6.9 -> normalized R7 conversion error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class LegacyCheckpointSource:
    checkpoint_id: str
    execution_id: str
    session_id: str
    iteration: int
    wait_reason: str
    parent_checkpoint_id: str | None
    origin_connection_id: str | None
    origin_client_id: str | None
    owner_user_id: str | None
    transcript: tuple[dict[str, Any], ...]
    metadata: Mapping[str, Any]

    @property
    def legacy_source_key(self) -> str:
        raw = f"phase6.9:{self.execution_id}:{self.checkpoint_id}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return f"phase6.9:{digest}"


def parse_legacy_checkpoint_source(
    execution,
    *,
    requested_checkpoint_id: str | None = None,
    target_user_id: str | None = None,
    target_client_id: str | None = None,
) -> LegacyCheckpointSource:
    """Parse only the current persisted Phase 6.9 WAITING checkpoint.

    This helper deliberately grants no replay authority. It only validates the
    shape and identity of the legacy source before R6-backed materialization.
    """

    raw_execution_state = getattr(execution, "state", "")
    execution_state = getattr(raw_execution_state, "value", raw_execution_state)
    raw_wait_reason = getattr(execution, "wait_reason", None)
    execution_wait_reason = getattr(raw_wait_reason, "value", raw_wait_reason)
    if str(execution_state) not in {"WAITING", "WAITING_FOR_CONNECTION"}:
        raise LegacyCheckpointMaterializationError(
            "EXECUTION_NOT_WAITING",
            "Legacy materialization requires a WAITING AgentExecution.",
        )
    if execution_wait_reason not in {None, "", "CONNECTION"}:
        raise LegacyCheckpointMaterializationError(
            "WAIT_REASON_MISMATCH",
            "Only legacy WAITING(CONNECTION) can be materialized by R7-I.",
        )
    if str(execution_state) == "WAITING" and execution_wait_reason != "CONNECTION":
        raise LegacyCheckpointMaterializationError(
            "WAIT_REASON_MISMATCH",
            "Normalized WAITING legacy input requires CONNECTION wait_reason.",
        )

    context_state = dict(getattr(execution, "context_state", None) or {})
    continuation = context_state.get("continuation")
    if not isinstance(continuation, Mapping):
        raise LegacyCheckpointMaterializationError(
            "CHECKPOINT_INCOMPLETE",
            "Legacy continuation state is missing.",
        )

    current_id = continuation.get("current_checkpoint_id")
    checkpoints = continuation.get("checkpoints")
    if not isinstance(current_id, str) or not current_id:
        raise LegacyCheckpointMaterializationError(
            "CHECKPOINT_INCOMPLETE",
            "Legacy continuation has no current checkpoint id.",
        )
    if requested_checkpoint_id and requested_checkpoint_id != current_id:
        raise LegacyCheckpointMaterializationError(
            "STALE_CHECKPOINT",
            "Requested checkpoint is not the legacy current checkpoint.",
        )
    if not isinstance(checkpoints, Mapping):
        raise LegacyCheckpointMaterializationError(
            "CHECKPOINT_INCOMPLETE",
            "Legacy continuation checkpoint map is missing.",
        )
    raw = checkpoints.get(current_id)
    if not isinstance(raw, Mapping):
        raise LegacyCheckpointMaterializationError(
            "CHECKPOINT_INCOMPLETE",
            "Legacy current checkpoint payload is missing.",
        )

    execution_id = raw.get("execution_id")
    session_id = raw.get("session_id")
    if execution_id != getattr(execution, "id", None):
        raise LegacyCheckpointMaterializationError(
            "LEGACY_CHECKPOINT_UNSAFE",
            "Legacy checkpoint execution identity does not match durable execution.",
        )
    if session_id != getattr(execution, "session_id", None):
        raise LegacyCheckpointMaterializationError(
            "LEGACY_CHECKPOINT_UNSAFE",
            "Legacy checkpoint session identity does not match durable execution.",
        )

    state = str(raw.get("state") or "")
    wait_reason = str(raw.get("wait_reason") or "")
    reason = str(raw.get("reason") or "")
    if state not in {"WAITING", "WAITING_FOR_CONNECTION"}:
        raise LegacyCheckpointMaterializationError(
            "LEGACY_CHECKPOINT_UNSAFE",
            "Legacy current checkpoint is not WAITING.",
        )
    if wait_reason not in {"", "CONNECTION"}:
        raise LegacyCheckpointMaterializationError(
            "WAIT_REASON_MISMATCH",
            "Legacy checkpoint wait reason is not CONNECTION.",
        )
    if reason and reason not in {
        "WAITING_FOR_CONNECTION",
        "CONNECTION_DISCONNECTED",
    }:
        raise LegacyCheckpointMaterializationError(
            "LEGACY_CHECKPOINT_UNSAFE",
            "Legacy checkpoint reason is not a reconnect safe point.",
        )

    metadata = dict(raw.get("metadata") or {})
    owner_user_id = metadata.get("owner_user_id")
    origin_client_id = metadata.get("origin_client_id")
    if target_user_id and owner_user_id and owner_user_id != target_user_id:
        raise LegacyCheckpointMaterializationError(
            "FOREIGN_PRINCIPAL",
            "Legacy checkpoint owner differs from authenticated principal.",
        )
    if target_client_id and origin_client_id and origin_client_id != target_client_id:
        raise LegacyCheckpointMaterializationError(
            "FOREIGN_CLIENT",
            "Legacy checkpoint belongs to another stable client installation.",
        )

    iteration = raw.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 0:
        raise LegacyCheckpointMaterializationError(
            "CHECKPOINT_INCOMPLETE",
            "Legacy checkpoint iteration is invalid.",
        )
    transcript_raw = raw.get("transcript")
    if not isinstance(transcript_raw, (list, tuple)):
        raise LegacyCheckpointMaterializationError(
            "CHECKPOINT_INCOMPLETE",
            "Legacy checkpoint transcript is missing.",
        )
    transcript: list[dict[str, Any]] = []
    for item in transcript_raw:
        if not isinstance(item, Mapping):
            raise LegacyCheckpointMaterializationError(
                "LEGACY_CHECKPOINT_UNSAFE",
                "Legacy transcript contains a non-object message.",
            )
        transcript.append(dict(item))

    return LegacyCheckpointSource(
        checkpoint_id=current_id,
        execution_id=execution_id,
        session_id=session_id,
        iteration=iteration,
        wait_reason="CONNECTION",
        parent_checkpoint_id=(
            str(raw["parent_checkpoint_id"])
            if raw.get("parent_checkpoint_id")
            else None
        ),
        origin_connection_id=(
            str(raw["origin_connection_id"])
            if raw.get("origin_connection_id")
            else None
        ),
        origin_client_id=(
            str(origin_client_id) if origin_client_id else None
        ),
        owner_user_id=(str(owner_user_id) if owner_user_id else None),
        transcript=tuple(transcript),
        metadata=metadata,
    )


def sanitize_legacy_transcript(
    transcript: Iterable[Mapping[str, Any]],
    *,
    active_tool_call_ids: Iterable[str],
) -> tuple[dict[str, Any], ...]:
    """Remove current-batch tool outcomes so R7 rebuilds them from authority."""

    active = set(active_tool_call_ids)
    result: list[dict[str, Any]] = []
    for raw in transcript:
        message = dict(raw)
        if message.get("role") == "tool":
            tool_call_id = message.get("tool_call_id")
            if not tool_call_id or tool_call_id in active:
                continue
        result.append(message)
    return tuple(result)


__all__ = [
    "LegacyCheckpointMaterializationError",
    "LegacyCheckpointSource",
    "parse_legacy_checkpoint_source",
    "sanitize_legacy_transcript",
]
