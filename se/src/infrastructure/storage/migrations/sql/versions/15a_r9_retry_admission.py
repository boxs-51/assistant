"""Add immutable AE-R9 RETRY admission receipts.

Revision ID: 15a_r9_retry_admission
Revises: 14d_r8_fork_runtime_seed
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "15a_r9_retry_admission"
down_revision: Union[str, None] = "14d_r8_fork_runtime_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_task_retry_admissions",
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("retry_request_id", sa.String(length=255), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("branch_id", sa.String(length=255), nullable=False),
        sa.Column("source_execution_id", sa.String(length=255), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["agent_tasks.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["agent_task_branches.branch_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_execution_id"],
            ["agent_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_checkpoint_id"],
            ["agent_execution_checkpoints.checkpoint_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("task_id", "retry_request_id"),
        sa.UniqueConstraint(
            "execution_id",
            name="uq_agent_task_retry_admissions_execution_id",
        ),
    )
    op.create_index(
        "ix_agent_task_retry_admissions_branch_id",
        "agent_task_retry_admissions",
        ["branch_id"],
    )
    op.create_index(
        "ix_agent_task_retry_admissions_source_execution_id",
        "agent_task_retry_admissions",
        ["source_execution_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_task_retry_admissions_source_execution_id",
        table_name="agent_task_retry_admissions",
    )
    op.drop_index(
        "ix_agent_task_retry_admissions_branch_id",
        table_name="agent_task_retry_admissions",
    )
    op.drop_table("agent_task_retry_admissions")
