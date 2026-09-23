from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AgentTaskRetryAdmissionRecord(Base):
    """Immutable committed-result authority for one logical AE-R9 RETRY request."""

    __tablename__ = "agent_task_retry_admissions"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            name="uq_agent_task_retry_admissions_execution_id",
        ),
        Index(
            "ix_agent_task_retry_admissions_branch_id",
            "branch_id",
        ),
        Index(
            "ix_agent_task_retry_admissions_source_execution_id",
            "source_execution_id",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    retry_request_id: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
    )

    plan_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    branch_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_task_branches.branch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_execution_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_executions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_checkpoint_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey(
            "agent_execution_checkpoints.checkpoint_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    execution_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_executions.id", ondelete="RESTRICT"),
        nullable=False,
    )

    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


__all__ = ["AgentTaskRetryAdmissionRecord"]
