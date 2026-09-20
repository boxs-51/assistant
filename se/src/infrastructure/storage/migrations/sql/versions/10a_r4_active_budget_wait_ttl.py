"""Add R4 active budget and WAITING TTL representation.

Revision ID: 10a_r4_active_budget_wait_ttl
Revises: 9a_r3_execution_lineage
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "10a_r4_active_budget_wait_ttl"
down_revision: Union[str, None] = "9a_r3_execution_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_executions",
        sa.Column(
            "remaining_active_budget_seconds",
            sa.Float(),
            nullable=True,
        ),
    )
    op.add_column(
        "agent_executions",
        sa.Column(
            "wait_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_agent_executions_wait_expires_at",
        "agent_executions",
        ["wait_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_executions_wait_expires_at",
        table_name="agent_executions",
    )
    op.drop_column("agent_executions", "wait_expires_at")
    op.drop_column(
        "agent_executions",
        "remaining_active_budget_seconds",
    )
