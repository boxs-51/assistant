from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AgentTaskAggregateAdmissionRecord(Base):
    """Immutable provenance for one explicit AE-R9 AGGREGATE execution."""

    __tablename__ = "agent_task_aggregate_admissions"
    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            name="uq_agent_task_aggregate_admissions_execution_id",
        ),
        Index(
            "ix_agent_task_aggregate_admissions_target_branch_id",
            "target_branch_id",
        ),
    )

    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    aggregate_request_id: Mapped[str] = mapped_column(
        String(255), primary_key=True
    )
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_seed_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    target_branch_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_task_branches.branch_id", ondelete="RESTRICT"),
        nullable=False,
    )
    execution_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_executions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_branch_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
    source_execution_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
    result_fingerprints: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["AgentTaskAggregateAdmissionRecord"]
