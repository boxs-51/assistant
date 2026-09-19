"""Normalize agent waiting state and add execution CAS columns.

Revision ID: 8a_agent_execution_waiting_cas
Revises: 7a_connection_affinity
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a_agent_execution_waiting_cas"
down_revision: Union[str, None] = "7a_connection_affinity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column("wait_reasons", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.execute(
        "UPDATE agent_tasks SET status = 'WAITING', "
        "wait_reasons = '[\"CONNECTION\"]' "
        "WHERE status = 'WAITING_FOR_CONNECTION'"
    )
    op.add_column(
        "agent_executions",
        sa.Column("wait_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "agent_executions",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE agent_executions "
        "SET state = 'WAITING', wait_reason = 'CONNECTION' "
        "WHERE state = 'WAITING_FOR_CONNECTION'"
    )
    op.execute(
        "UPDATE agent_executions "
        "SET state = 'WAITING', wait_reason = 'AGENT' "
        "WHERE state = 'WAITING_AGENT'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE agent_tasks SET status = 'WAITING_FOR_CONNECTION' "
        "WHERE status = 'WAITING'"
    )
    op.execute(
        "UPDATE agent_executions SET state = 'WAITING_FOR_CONNECTION' "
        "WHERE state = 'WAITING' AND wait_reason = 'CONNECTION'"
    )
    op.execute(
        "UPDATE agent_executions SET state = 'WAITING_AGENT' "
        "WHERE state = 'WAITING' AND wait_reason = 'AGENT'"
    )
    op.drop_column("agent_executions", "completed_at")
    op.drop_column("agent_executions", "started_at")
    op.drop_column("agent_executions", "revision")
    op.drop_column("agent_executions", "wait_reason")
    op.drop_column("agent_tasks", "wait_reasons")
