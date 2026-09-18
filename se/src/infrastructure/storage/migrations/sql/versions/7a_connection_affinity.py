"""Persist explicit client connection affinity for agent tasks.

Revision ID: 7a_connection_affinity
Revises: 6a_capability_invocations
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a_connection_affinity"
down_revision: Union[str, None] = "6a_capability_invocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column("connection_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_tasks",
        sa.Column("client_id", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_agent_tasks_connection_id",
        "agent_tasks",
        ["connection_id"],
    )
    op.create_index("ix_agent_tasks_client_id", "agent_tasks", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_tasks_client_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_connection_id", table_name="agent_tasks")
    op.drop_column("agent_tasks", "client_id")
    op.drop_column("agent_tasks", "connection_id")
