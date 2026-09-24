from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AgentTaskBranchRecord(Base):
    __tablename__ = "agent_task_branches"
    __table_args__ = (
        CheckConstraint(
            "revision >= 0",
            name="ck_agent_task_branches_revision_nonnegative",
        ),
        CheckConstraint(
            "resolution_state IN "
            "('OPEN', 'ADOPTED', 'SUPERSEDED', 'DISCARDED', 'CANCELLED')",
            name="ck_agent_task_branches_resolution_state",
        ),
        CheckConstraint(
            "parent_branch_id IS NULL OR parent_branch_id <> branch_id",
            name="ck_agent_task_branches_parent_not_self",
        ),
        CheckConstraint(
            "("
            "parent_branch_id IS NULL "
            "AND base_execution_id IS NULL "
            "AND base_checkpoint_id IS NULL"
            ") OR ("
            "parent_branch_id IS NOT NULL "
            "AND base_execution_id IS NOT NULL "
            "AND base_checkpoint_id IS NOT NULL"
            ")",
            name="ck_agent_task_branches_origin_shape",
        ),
        Index(
            "ix_agent_task_branches_task_resolution",
            "task_id",
            "resolution_state",
        ),
        Index(
            "ix_agent_task_branches_task_created_branch",
            "task_id",
            "created_at",
            "branch_id",
        ),
    )

    branch_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_branch_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_task_branches.branch_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    base_execution_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_executions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    base_checkpoint_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey(
            "agent_execution_checkpoints.checkpoint_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )
    current_execution_id: Mapped[str | None] = mapped_column(
        String(255),
        ForeignKey("agent_executions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resolution_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="OPEN",
        server_default="OPEN",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentTaskBranchContextRecord(Base):
    __tablename__ = "agent_task_branch_contexts"
    __table_args__ = (
        CheckConstraint(
            "revision >= 0",
            name="ck_agent_task_branch_contexts_revision_nonnegative",
        ),
    )

    branch_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_task_branches.branch_id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    overlay_messages: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = ["AgentTaskBranchRecord", "AgentTaskBranchContextRecord"]
