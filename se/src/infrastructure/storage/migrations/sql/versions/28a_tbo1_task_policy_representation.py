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
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "task_mode",
                sa.String(length=16),
                nullable=False,
                server_default="FINITE",
            )
        )
        batch_op.add_column(
            sa.Column("task_horizon_at", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("review_horizon_at", sa.Float(), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_agent_tasks_task_mode",
            "task_mode IN ('FINITE', 'RECURRING')",
        )
        batch_op.create_check_constraint(
            "ck_agent_tasks_task_horizon_nonnegative",
            "task_horizon_at IS NULL OR task_horizon_at >= 0",
        )
        batch_op.create_check_constraint(
            "ck_agent_tasks_review_horizon_nonnegative",
            "review_horizon_at IS NULL OR review_horizon_at >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch_op:
        batch_op.drop_constraint(
            "ck_agent_tasks_review_horizon_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_agent_tasks_task_horizon_nonnegative",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_agent_tasks_task_mode",
            type_="check",
        )
        batch_op.drop_column("review_horizon_at")
        batch_op.drop_column("task_horizon_at")
        batch_op.drop_column("task_mode")
