"""Add R3 AgentExecution lineage representation.

Revision ID: 9a_r3_execution_lineage
Revises: 8a_agent_execution_waiting_cas
"""

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a_r3_execution_lineage"
down_revision: Union[str, None] = "8a_agent_execution_waiting_cas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_executions",
        sa.Column("branch_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("retry_of_execution_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("base_execution_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "agent_executions",
        sa.Column("base_checkpoint_id", sa.String(length=255), nullable=True),
    )

    op.create_index(
        "ix_agent_executions_branch_id",
        "agent_executions",
        ["branch_id"],
    )
    op.create_index(
        "ix_agent_executions_retry_of_execution_id",
        "agent_executions",
        ["retry_of_execution_id"],
    )
    op.create_index(
        "ix_agent_executions_base_execution_id",
        "agent_executions",
        ["base_execution_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_executions_base_execution_id",
        table_name="agent_executions",
    )
    op.drop_index(
        "ix_agent_executions_retry_of_execution_id",
        table_name="agent_executions",
    )
    op.drop_index(
        "ix_agent_executions_branch_id",
        table_name="agent_executions",
    )
    op.drop_column("agent_executions", "base_checkpoint_id")
    op.drop_column("agent_executions", "base_execution_id")
    op.drop_column("agent_executions", "retry_of_execution_id")
    op.drop_column("agent_executions", "branch_id")