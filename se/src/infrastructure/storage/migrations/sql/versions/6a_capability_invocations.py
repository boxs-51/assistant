"""add canonical capability invocation persistence

Revision ID: 6a_capability_invocations
Revises: 5b_conversation_temporal_contract
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6a_capability_invocations"
down_revision: Union[str, None] = "5b_conversation_temporal_contract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "capability_invocations",
        sa.Column("invocation_id", sa.String(255), primary_key=True),
        sa.Column("capability_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("execution_mode", sa.String(32), nullable=False),
        sa.Column("implementation_id", sa.String(255)),
        sa.Column("driver_kind", sa.String(100)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("wait_reason", sa.String(32)),
        sa.Column("session_id", sa.String(255)),
        sa.Column("turn_id", sa.String(255)),
        sa.Column("execution_id", sa.String(255)),
        sa.Column("workflow_id", sa.String(255)),
        sa.Column("tool_call_id", sa.String(255)),
        sa.Column("connection_id", sa.String(255)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.String(255)),
        sa.Column("trace_id", sa.String(255)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    for column in (
        "capability_id", "implementation_id", "state", "session_id", "turn_id",
        "execution_id", "connection_id", "correlation_id", "trace_id",
    ):
        op.create_index(f"ix_capability_invocations_{column}", "capability_invocations", [column])

    op.create_table(
        "capability_invocation_attempts",
        sa.Column("attempt_id", sa.String(255), primary_key=True),
        sa.Column(
            "invocation_id",
            sa.String(255),
            sa.ForeignKey("capability_invocations.invocation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("implementation_id", sa.String(255), nullable=False),
        sa.Column("driver_kind", sa.String(100), nullable=False),
        sa.Column("connection_id", sa.String(255)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.JSON()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("invocation_id", "attempt_number", name="uq_capability_attempt_number"),
    )
    for column in ("invocation_id", "implementation_id", "connection_id", "state"):
        op.create_index(
            f"ix_capability_invocation_attempts_{column}",
            "capability_invocation_attempts",
            [column],
        )


def downgrade() -> None:
    op.drop_table("capability_invocation_attempts")
    op.drop_table("capability_invocations")
