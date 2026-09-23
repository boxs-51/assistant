"""Add immutable R8 FORK admission receipts.

Revision ID: 14c_r8_fork_admission
Revises: 14b_r8_root_branch_accounting
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "14c_r8_fork_admission"
down_revision: Union[str, None] = "14b_r8_root_branch_accounting"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_task_fork_admissions",
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("fork_request_id", sa.String(length=255), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_branch_id", sa.String(length=255), nullable=False),
        sa.Column("source_execution_id", sa.String(length=255), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("branch_id", sa.String(length=255), nullable=False),
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
            ["source_branch_id"],
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
            ["branch_id"],
            ["agent_task_branches.branch_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["agent_executions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "task_id",
            "fork_request_id",
        ),
        sa.UniqueConstraint(
            "branch_id",
            name="uq_agent_task_fork_admissions_branch_id",
        ),
        sa.UniqueConstraint(
            "execution_id",
            name="uq_agent_task_fork_admissions_execution_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_task_fork_admissions")
