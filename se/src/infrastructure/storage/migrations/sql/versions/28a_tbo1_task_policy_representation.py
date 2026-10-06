"""Add durable TBO-1 Task policy representation.

Revision ID: 28a_tbo1_task_policy_representation
Revises: 27a_cas_f7_t_tool_media_projection
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "28a_tbo1_task_policy_representation"
down_revision: Union[str, None] = "27a_cas_f7_t_tool_media_projection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column(
            "task_mode",
            sa.String(length=16),
            sa.CheckConstraint(
                "task_mode IN ('FINITE', 'RECURRING')",
                name="ck_agent_tasks_task_mode",
            ),
            nullable=False,
            server_default="FINITE",
        ),
    )
    op.add_column(
        "agent_tasks",
        sa.Column(
            "task_horizon_at",
            sa.Float(),
            sa.CheckConstraint(
                "task_horizon_at IS NULL OR task_horizon_at >= 0",
                name="ck_agent_tasks_task_horizon_nonnegative",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_tasks",
        sa.Column(
            "review_horizon_at",
            sa.Float(),
            sa.CheckConstraint(
                "review_horizon_at IS NULL OR review_horizon_at >= 0",
                name="ck_agent_tasks_review_horizon_nonnegative",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "review_horizon_at")
    op.drop_column("agent_tasks", "task_horizon_at")
    op.drop_column("agent_tasks", "task_mode")
