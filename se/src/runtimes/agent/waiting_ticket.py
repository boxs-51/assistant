from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


def build_waiting_ticket_payload(
    execution: Any,
    checkpoint: Any,
    pending_invocations: Iterable[Any],
) -> dict[str, Any]:
    """Project normalized durable WAITING state into the R7-H wire ticket.

    This helper is read-only. It neither builds a ResumePlan nor creates a
    ResumeClaim, and therefore cannot acquire AgentExecution authority.
    """

    if execution is None or checkpoint is None:
        raise ValueError("Normalized WAITING ticket requires execution/checkpoint")
    if str(getattr(execution, "state", "")) != "WAITING":
        raise ValueError("Only WAITING executions can publish resume tickets")
    execution_id = str(getattr(execution, "id", None) or getattr(execution, "execution_id", ""))
    checkpoint_id = str(getattr(checkpoint, "checkpoint_id", ""))
    current_checkpoint_id = str(getattr(execution, "current_checkpoint_id", "") or "")
    if not execution_id or not checkpoint_id or current_checkpoint_id != checkpoint_id:
        raise ValueError("WAITING ticket checkpoint is not current")
    revision = int(getattr(execution, "revision"))
    if int(getattr(checkpoint, "execution_revision")) != revision:
        raise ValueError("WAITING ticket checkpoint revision is stale")

    execution_wait_reason = str(getattr(execution, "wait_reason", "") or "")
    checkpoint_wait_reason = str(getattr(checkpoint, "wait_reason", "") or "")
    if not execution_wait_reason or execution_wait_reason != checkpoint_wait_reason:
        raise ValueError("WAITING ticket wait_reason is inconsistent")

    capability_ids: list[str] = []
    seen: set[str] = set()
    for item in pending_invocations:
        capability_id = str(getattr(item, "capability_id", "") or "")
        if capability_id and capability_id not in seen:
            seen.add(capability_id)
            capability_ids.append(capability_id)

    origin_client_id = getattr(checkpoint, "origin_client_id", None)
    if origin_client_id is not None:
        origin_client_id = str(origin_client_id)

    wait_expires_at = getattr(checkpoint, "wait_expires_at", None)
    if wait_expires_at is None:
        wait_expires_at = getattr(execution, "wait_expires_at", None)

    return {
        "execution_id": execution_id,
        "checkpoint_id": checkpoint_id,
        "revision": revision,
        "wait_reason": execution_wait_reason,
        "wait_expires_at": _iso_utc(wait_expires_at),
        "origin_client_id": origin_client_id,
        "pending_capability_ids": capability_ids,
        "auto_resume_allowed": (
            execution_wait_reason == "CONNECTION"
            and bool(origin_client_id)
            and bool(capability_ids)
        ),
    }


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


__all__ = ["build_waiting_ticket_payload"]
