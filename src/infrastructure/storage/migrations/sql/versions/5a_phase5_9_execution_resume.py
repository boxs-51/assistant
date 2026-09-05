"""add Phase 5.9 execution checkpoints and resume state

Revision ID: 5a_phase5_9_execution_resume
Revises: 4f_phase4_multi_agent
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5a_phase5_9_execution_resume"
down_revision: Union[str, None] = "4f_phase4_multi_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_executions", sa.Column("context_state", sa.JSON(), nullable=True))
    op.add_column("agent_executions", sa.Column("transcript", sa.JSON(), nullable=True))
    op.add_column("agent_executions", sa.Column("inference_request", sa.JSON(), nullable=True))
    op.add_column("agent_executions", sa.Column("inference_response", sa.JSON(), nullable=True))

    op.create_table(
        "agent_iterations",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("inference_request_id", sa.String(length=255), nullable=True),
        sa.Column("tool_call_ids", sa.JSON(), nullable=True),
        sa.Column("transcript", sa.JSON(), nullable=True),
        sa.Column("inference_request", sa.JSON(), nullable=True),
        sa.Column("inference_response", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["execution_id"], ["agent_executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_iterations_execution_id", "agent_iterations", ["execution_id"])
    op.create_index("ix_agent_iterations_inference_request_id", "agent_iterations", ["inference_request_id"])

    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("iteration_id", sa.String(length=255), nullable=False),
        sa.Column("invocation_id", sa.String(length=255), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("capability_id", sa.String(length=255), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("retry_state", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["execution_id"], ["agent_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["iteration_id"], ["agent_iterations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "tool_call_id", name="uq_agent_tool_call_execution_call"),
    )
    op.create_index("ix_agent_tool_calls_execution_id", "agent_tool_calls", ["execution_id"])
    op.create_index("ix_agent_tool_calls_iteration_id", "agent_tool_calls", ["iteration_id"])
    op.create_index("ix_agent_tool_calls_invocation_id", "agent_tool_calls", ["invocation_id"])
    op.create_index("ix_agent_tool_calls_tool_call_id", "agent_tool_calls", ["tool_call_id"])
    op.create_index("ix_agent_tool_calls_capability_id", "agent_tool_calls", ["capability_id"])

    op.create_table(
        "agent_tool_results",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("iteration_id", sa.String(length=255), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("invocation_id", sa.String(length=255), nullable=False),
        sa.Column("capability_id", sa.String(length=255), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["execution_id"], ["agent_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["iteration_id"], ["agent_iterations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "tool_call_id", name="uq_agent_tool_result_execution_call"),
    )
    op.create_index("ix_agent_tool_results_execution_id", "agent_tool_results", ["execution_id"])
    op.create_index("ix_agent_tool_results_iteration_id", "agent_tool_results", ["iteration_id"])
    op.create_index("ix_agent_tool_results_tool_call_id", "agent_tool_results", ["tool_call_id"])
    op.create_index("ix_agent_tool_results_invocation_id", "agent_tool_results", ["invocation_id"])
    op.create_index("ix_agent_tool_results_capability_id", "agent_tool_results", ["capability_id"])


def downgrade() -> None:
    op.drop_table("agent_tool_results")
    op.drop_table("agent_tool_calls")
    op.drop_table("agent_iterations")
    op.drop_column("agent_executions", "inference_response")
    op.drop_column("agent_executions", "inference_request")
    op.drop_column("agent_executions", "transcript")
    op.drop_column("agent_executions", "context_state")