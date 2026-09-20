from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class CapabilityInvocationRecord(Base):
    __tablename__ = "capability_invocations"
    __table_args__ = (
        CheckConstraint(
            "idempotency IN "
            "('IDEMPOTENT', 'DEDUPLICATED', 'NON_IDEMPOTENT', 'UNKNOWN')",
            name="ck_capability_invocations_idempotency",
        ),
        CheckConstraint(
            "remote_outcome_state IS NULL OR remote_outcome_state IN "
            "('NOT_DISPATCHED', 'IN_FLIGHT', 'OUTCOME_UNKNOWN', "
            "'TERMINAL_COMMITTED')",
            name="ck_capability_invocations_remote_outcome_state",
        ),
    )

    invocation_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    capability_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    capability_version: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="UNKNOWN",
        server_default="UNKNOWN",
    )
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    owner_user_id: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
    )
    origin_client_id: Mapped[str | None] = mapped_column(
        String(255),
        index=True,
    )
    remote_outcome_state: Mapped[str | None] = mapped_column(
        String(32),
        index=True,
    )
    implementation_id: Mapped[str | None] = mapped_column(String(255), index=True)
    driver_kind: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    wait_reason: Mapped[str | None] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(String(255), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(255), index=True)
    execution_id: Mapped[str | None] = mapped_column(String(255), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(255))
    tool_call_id: Mapped[str | None] = mapped_column(String(255))
    connection_id: Mapped[str | None] = mapped_column(String(255), index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    output: Mapped[Any | None] = mapped_column(JSON)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str | None] = mapped_column(String(255), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(255), index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CapabilityInvocationAttemptRecord(Base):
    __tablename__ = "capability_invocation_attempts"
    __table_args__ = (
        UniqueConstraint("invocation_id", "attempt_number", name="uq_capability_attempt_number"),
    )

    attempt_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    invocation_id: Mapped[str] = mapped_column(
        ForeignKey("capability_invocations.invocation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    implementation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    driver_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(255), index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
