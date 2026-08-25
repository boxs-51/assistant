"""add durable multi-agent state

Revision ID: 4f_phase4_multi_agent
Revises: 2b8eaa45108e
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "4f_phase4_multi_agent"
down_revision: Union[str, None] = "2b8eaa45108e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_sessions_owner_user_id", "agent_sessions", ["owner_user_id"])

    op.create_table(
        "agent_session_members",
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("session_id", "agent_id"),
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("sender_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_id", sa.String(length=255), nullable=True),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_messages_session_id", "agent_messages", ["session_id"])

    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("assigned_agent_id", sa.String(length=255), nullable=False),
        sa.Column("parent_task_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tasks_session_id", "agent_tasks", ["session_id"])
    op.create_index("ix_agent_tasks_assigned_agent_id", "agent_tasks", ["assigned_agent_id"])

    op.create_table(
        "agent_executions",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("parent_execution_id", sa.String(length=255), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_executions_session_id", "agent_executions", ["session_id"])
    op.create_index("ix_agent_executions_agent_id", "agent_executions", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_executions_agent_id", table_name="agent_executions")
    op.drop_index("ix_agent_executions_session_id", table_name="agent_executions")
    op.drop_table("agent_executions")
    op.drop_index("ix_agent_tasks_assigned_agent_id", table_name="agent_tasks")
    op.drop_index("ix_agent_tasks_session_id", table_name="agent_tasks")
    op.drop_table("agent_tasks")
    op.drop_index("ix_agent_messages_session_id", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_table("agent_session_members")
    op.drop_index("ix_agent_sessions_owner_user_id", table_name="agent_sessions")
    op.drop_table("agent_sessions")
