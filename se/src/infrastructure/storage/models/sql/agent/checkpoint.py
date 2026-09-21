from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AgentExecutionCheckpointRecord(Base):
    __tablename__ = "agent_execution_checkpoints"
    __table_args__ = (
        CheckConstraint(
            "execution_revision >= 0",
            name="ck_agent_execution_checkpoints_revision_nonnegative",
        ),
        CheckConstraint(
            "iteration >= 0",
            name="ck_agent_execution_checkpoints_iteration_nonnegative",
        ),
        CheckConstraint(
            "remaining_active_budget_seconds IS NULL "
            "OR remaining_active_budget_seconds >= 0",
            name="ck_agent_execution_checkpoints_budget_nonnegative",
        ),
        CheckConstraint(
            "transcript_version IS NULL OR transcript_version >= 0",
            name="ck_agent_execution_checkpoints_transcript_version_nonnegative",
        ),
        CheckConstraint(
            "transcript_snapshot IS NOT NULL OR transcript_ref IS NOT NULL",
            name="ck_agent_execution_checkpoints_transcript_reconstructable",
        ),
        UniqueConstraint(
            "legacy_source_key",
            name="uq_agent_execution_checkpoints_legacy_source_key",
        ),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    execution_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    branch_id: Mapped[str | None] = mapped_column(String(255), index=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(255), index=True)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wait_reason: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    remaining_active_budget_seconds: Mapped[float | None] = mapped_column(Float)
    wait_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    origin_client_id: Mapped[str | None] = mapped_column(String(255), index=True)
    origin_connection_id: Mapped[str | None] = mapped_column(String(255), index=True)
    transcript_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    transcript_ref: Mapped[str | None] = mapped_column(String(255))
    transcript_version: Mapped[int | None] = mapped_column(Integer)
    side_effect_watermark: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    legacy_source_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentCheckpointPendingInvocationRecord(Base):
    __tablename__ = "agent_checkpoint_pending_invocations"
    __table_args__ = (
        CheckConstraint(
            "ordinal >= 0",
            name="ck_agent_checkpoint_pending_invocations_ordinal_nonnegative",
        ),
        CheckConstraint(
            "invocation_revision >= 0",
            name="ck_agent_checkpoint_pending_invocations_revision_nonnegative",
        ),
        CheckConstraint(
            "idempotency IN "
            "('IDEMPOTENT', 'DEDUPLICATED', 'NON_IDEMPOTENT', 'UNKNOWN')",
            name="ck_agent_checkpoint_pending_invocations_idempotency",
        ),
        CheckConstraint(
            "observed_remote_outcome_state IS NULL OR "
            "observed_remote_outcome_state IN "
            "('NOT_DISPATCHED', 'IN_FLIGHT', 'OUTCOME_UNKNOWN', "
            "'TERMINAL_COMMITTED')",
            name="ck_agent_checkpoint_pending_invocations_remote_outcome",
        ),
        UniqueConstraint(
            "checkpoint_id",
            "invocation_id",
            name="uq_agent_checkpoint_pending_invocation",
        ),
        UniqueConstraint(
            "checkpoint_id",
            "tool_call_id",
            name="uq_agent_checkpoint_pending_tool_call",
        ),
    )

    checkpoint_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_execution_checkpoints.checkpoint_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    invocation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    invocation_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    capability_version: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    idempotency: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UNKNOWN", server_default="UNKNOWN"
    )
    observed_remote_outcome_state: Mapped[str | None] = mapped_column(String(32))
    origin_client_id: Mapped[str | None] = mapped_column(String(255))
    origin_connection_id: Mapped[str | None] = mapped_column(String(255))
