from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping
import uuid


class ResumeTicketState(str, Enum):
    OBSERVED = "OBSERVED"
    BLOCKED = "BLOCKED"
    ELIGIBLE = "ELIGIBLE"
    IN_FLIGHT = "IN_FLIGHT"
    RETRY_SAME_REQUEST = "RETRY_SAME_REQUEST"
    WAIT_REFRESH = "WAIT_REFRESH"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    CONFLICT = "CONFLICT"


_TERMINAL_STATES = frozenset(
    {
        ResumeTicketState.ACCEPTED,
        ResumeTicketState.REJECTED,
        ResumeTicketState.FAILED,
        ResumeTicketState.EXPIRED,
        ResumeTicketState.SUPERSEDED,
        ResumeTicketState.CONFLICT,
    }
)


@dataclass(frozen=True, slots=True)
class PendingResumeTicket:
    """Server-derived resumable-work metadata; never execution authority."""

    execution_id: str
    checkpoint_id: str
    revision: int
    wait_reason: str
    pending_capability_ids: tuple[str, ...]
    origin_client_id: str | None
    wait_expires_at: datetime | None
    auto_resume_allowed: bool

    @property
    def key(self) -> tuple[str, str]:
        return self.execution_id, self.checkpoint_id

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> "PendingResumeTicket":
        execution_id = str(payload.get("execution_id") or "")
        checkpoint_id = str(payload.get("checkpoint_id") or "")
        revision = payload.get("revision")
        wait_reason = str(payload.get("wait_reason") or "")
        if not execution_id or not checkpoint_id:
            raise ValueError("PendingResumeTicket requires execution_id/checkpoint_id")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("PendingResumeTicket requires authoritative revision")
        if not wait_reason:
            raise ValueError("PendingResumeTicket requires wait_reason")

        pending_raw = payload.get("pending_capability_ids") or ()
        if not isinstance(pending_raw, (list, tuple)):
            raise ValueError("pending_capability_ids must be a list/tuple")
        pending: list[str] = []
        seen: set[str] = set()
        for item in pending_raw:
            capability_id = str(item or "")
            if capability_id and capability_id not in seen:
                seen.add(capability_id)
                pending.append(capability_id)

        wait_expires_at = _parse_datetime(payload.get("wait_expires_at"))
        origin_client_id = payload.get("origin_client_id")
        if origin_client_id is not None:
            origin_client_id = str(origin_client_id)

        return cls(
            execution_id=execution_id,
            checkpoint_id=checkpoint_id,
            revision=revision,
            wait_reason=wait_reason,
            pending_capability_ids=tuple(pending),
            origin_client_id=origin_client_id,
            wait_expires_at=wait_expires_at,
            auto_resume_allowed=bool(payload.get("auto_resume_allowed", False)),
        )


@dataclass(slots=True)
class PendingResumeEntry:
    ticket: PendingResumeTicket
    principal_id: str | None
    state: ResumeTicketState = ResumeTicketState.OBSERVED
    active_resume_request_id: str | None = None
    attempt_generation: int | None = None
    attempt_connection_id: str | None = None
    last_outcome_code: str | None = None
    freshness_epoch: int = 0

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def replace_ticket(self, ticket: PendingResumeTicket) -> None:
        if ticket.key != self.ticket.key:
            raise ValueError("Cannot replace a resume entry with another ticket key")
        self.ticket = ticket
        self.freshness_epoch += 1

    def ensure_resume_request_id(
        self,
        factory: Callable[[], str] | None = None,
    ) -> str:
        if self.active_resume_request_id is None:
            maker = factory or (lambda: f"rr-{uuid.uuid4().hex}")
            self.active_resume_request_id = str(maker())
        return self.active_resume_request_id

    def clear_resume_request_for_new_attempt(self) -> None:
        self.active_resume_request_id = None
        self.attempt_generation = None
        self.attempt_connection_id = None

    def transition(self, state: ResumeTicketState) -> None:
        if self.terminal and state is not self.state:
            raise ValueError(
                f"Terminal resume ticket {self.state.value} cannot transition to {state.value}"
            )
        self.state = state


@dataclass(frozen=True, slots=True)
class ResumeProtocolOutcome:
    kind: str
    execution_id: str
    checkpoint_id: str
    resume_request_id: str
    code: str | None = None
    retryable: bool = False
    claim_id: str | None = None
    accepted_revision: int | None = None
    recovery_checkpoint_id: str | None = None
    recovery_revision: int | None = None
    wait_reason: str | None = None
    payload: Mapping[str, Any] | None = None

    @classmethod
    def from_envelope(cls, envelope: Mapping[str, Any]) -> "ResumeProtocolOutcome":
        message_type = str(envelope.get("type") or "")
        kinds = {
            "execution.resume.accepted": "ACCEPTED",
            "execution.resume.rejected": "REJECTED",
            "execution.resume.failed": "FAILED",
        }
        if message_type not in kinds:
            raise ValueError(f"Unsupported resume outcome type: {message_type}")
        payload = dict(envelope.get("payload") or {})
        execution_id = str(payload.get("execution_id") or envelope.get("execution_id") or "")
        checkpoint_id = str(payload.get("checkpoint_id") or "")
        resume_request_id = str(payload.get("resume_request_id") or "")
        if not execution_id or not checkpoint_id or not resume_request_id:
            raise ValueError("Resume outcome lacks canonical correlation fields")
        return cls(
            kind=kinds[message_type],
            execution_id=execution_id,
            checkpoint_id=checkpoint_id,
            resume_request_id=resume_request_id,
            code=(str(payload["code"]) if payload.get("code") is not None else None),
            retryable=bool(payload.get("retryable", False)),
            claim_id=(str(payload["claim_id"]) if payload.get("claim_id") is not None else None),
            accepted_revision=_optional_int(payload.get("accepted_revision")),
            recovery_checkpoint_id=(
                str(payload["recovery_checkpoint_id"])
                if payload.get("recovery_checkpoint_id") is not None
                else None
            ),
            recovery_revision=_optional_int(
                payload.get("recovery_revision", payload.get("revision"))
            ),
            wait_reason=(str(payload["wait_reason"]) if payload.get("wait_reason") is not None else None),
            payload=payload,
        )


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Resume revision must be an integer")
    return value


__all__ = [
    "PendingResumeEntry",
    "PendingResumeTicket",
    "ResumeProtocolOutcome",
    "ResumeTicketState",
]
