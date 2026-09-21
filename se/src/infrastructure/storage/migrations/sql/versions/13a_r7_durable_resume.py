"""Add R7 durable checkpoint, ResumeClaim and tool-result certainty schema.

Revision ID: 13a_r7_durable_resume
Revises: 12a_r6_remote_reconciliation
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "13a_r7_durable_resume"
down_revision: Union[str, None] = "12a_r6_remote_reconciliation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_executions") as batch_op:
        batch_op.add_column(
            sa.Column("current_checkpoint_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("bound_client_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("bound_connection_id", sa.String(length=255), nullable=True)
        )
        batch_op.create_index(
            "ix_agent_executions_current_checkpoint_id",
            ["current_checkpoint_id"],
        )
        batch_op.create_index(
            "ix_agent_executions_bound_client_id",
            ["bound_client_id"],
        )
        batch_op.create_index(
            "ix_agent_executions_bound_connection_id",
            ["bound_connection_id"],
        )

    with op.batch_alter_table("agent_tool_results") as batch_op:
        batch_op.add_column(
            sa.Column(
                "commit_state",
                sa.String(length=16),
                nullable=False,
                server_default="PROVISIONAL",
            )
        )
        batch_op.create_check_constraint(
            "ck_agent_tool_results_commit_state",
            "commit_state IN ('PROVISIONAL', 'COMMITTED')",
        )
        batch_op.create_index(
            "ix_agent_tool_results_commit_state",
            ["commit_state"],
        )

    # Historical tool rows predate the R7 certainty projection.  Promote only
    # rows whose existing representation already proves a terminal outcome.
    # Ambiguous R6/legacy remote-disconnect rows remain PROVISIONAL.
    op.execute(
        sa.text(
            """
            UPDATE agent_tool_results
            SET commit_state = 'COMMITTED'
            WHERE success = true
               OR (
                    success = false
                    AND error_code IS NOT NULL
                    AND error_code NOT IN (
                        'REMOTE_CONNECTION_LOST',
                        'REMOTE_OUTCOME_UNKNOWN',
                        'REMOTE_RESULT_RECONCILIATION_REQUIRED'
                    )
                    AND (
                        metadata IS NULL
                        OR CAST(metadata AS TEXT) NOT LIKE '%REMOTE_CONNECTION_LOST%'
                    )
               )
            """
        )
    )

    op.create_table(
        "agent_execution_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("execution_revision", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("branch_id", sa.String(length=255), nullable=True),
        sa.Column("parent_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("wait_reason", sa.String(length=32), nullable=False),
        sa.Column("remaining_active_budget_seconds", sa.Float(), nullable=True),
        sa.Column("wait_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("origin_client_id", sa.String(length=255), nullable=True),
        sa.Column("origin_connection_id", sa.String(length=255), nullable=True),
        sa.Column("transcript_snapshot", sa.JSON(), nullable=True),
        sa.Column("transcript_ref", sa.String(length=255), nullable=True),
        sa.Column("transcript_version", sa.Integer(), nullable=True),
        sa.Column("side_effect_watermark", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("legacy_source_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "execution_revision >= 0",
            name="ck_agent_execution_checkpoints_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "iteration >= 0",
            name="ck_agent_execution_checkpoints_iteration_nonnegative",
        ),
        sa.CheckConstraint(
            "remaining_active_budget_seconds IS NULL "
            "OR remaining_active_budget_seconds >= 0",
            name="ck_agent_execution_checkpoints_budget_nonnegative",
        ),
        sa.CheckConstraint(
            "transcript_version IS NULL OR transcript_version >= 0",
            name="ck_agent_execution_checkpoints_transcript_version_nonnegative",
        ),
        sa.CheckConstraint(
            "transcript_snapshot IS NOT NULL OR transcript_ref IS NOT NULL",
            name="ck_agent_execution_checkpoints_transcript_reconstructable",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["agent_executions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("checkpoint_id"),
        sa.UniqueConstraint(
            "legacy_source_key",
            name="uq_agent_execution_checkpoints_legacy_source_key",
        ),
    )
    for name, columns in (
        ("ix_agent_execution_checkpoints_execution_id", ["execution_id"]),
        ("ix_agent_execution_checkpoints_session_id", ["session_id"]),
        ("ix_agent_execution_checkpoints_task_id", ["task_id"]),
        ("ix_agent_execution_checkpoints_branch_id", ["branch_id"]),
        ("ix_agent_execution_checkpoints_parent_checkpoint_id", ["parent_checkpoint_id"]),
        ("ix_agent_execution_checkpoints_wait_reason", ["wait_reason"]),
        ("ix_agent_execution_checkpoints_wait_expires_at", ["wait_expires_at"]),
        ("ix_agent_execution_checkpoints_origin_client_id", ["origin_client_id"]),
        ("ix_agent_execution_checkpoints_origin_connection_id", ["origin_connection_id"]),
    ):
        op.create_index(name, "agent_execution_checkpoints", columns)

    op.create_table(
        "agent_checkpoint_pending_invocations",
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("invocation_id", sa.String(length=255), nullable=False),
        sa.Column("invocation_revision", sa.Integer(), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("capability_id", sa.String(length=255), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_agent_checkpoint_pending_invocations_ordinal_nonnegative",
        ),
        sa.CheckConstraint(
            "invocation_revision >= 0",
            name="ck_agent_checkpoint_pending_invocations_revision_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["agent_execution_checkpoints.checkpoint_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("checkpoint_id", "ordinal"),
        sa.UniqueConstraint(
            "checkpoint_id",
            "invocation_id",
            name="uq_agent_checkpoint_pending_invocation",
        ),
        sa.UniqueConstraint(
            "checkpoint_id",
            "tool_call_id",
            name="uq_agent_checkpoint_pending_tool_call",
        ),
    )
    op.create_index(
        "ix_agent_checkpoint_pending_invocations_invocation_id",
        "agent_checkpoint_pending_invocations",
        ["invocation_id"],
    )
    op.create_index(
        "ix_agent_checkpoint_pending_invocations_tool_call_id",
        "agent_checkpoint_pending_invocations",
        ["tool_call_id"],
    )
    op.create_index(
        "ix_agent_checkpoint_pending_invocations_capability_id",
        "agent_checkpoint_pending_invocations",
        ["capability_id"],
    )

    op.create_table(
        "agent_resume_claims",
        sa.Column("claim_id", sa.String(length=255), nullable=False),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=False),
        sa.Column("resume_request_id", sa.String(length=255), nullable=False),
        sa.Column("expected_execution_revision", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=True),
        sa.Column("connection_id", sa.String(length=255), nullable=True),
        sa.Column("wait_reason", sa.String(length=32), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="CREATED"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("rejection_code", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_execution_revision", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "state IN ('CREATED', 'CONSUMED', 'REJECTED', 'EXPIRED')",
            name="ck_agent_resume_claims_state",
        ),
        sa.CheckConstraint(
            "revision >= 0", name="ck_agent_resume_claims_revision_nonnegative"
        ),
        sa.CheckConstraint(
            "expected_execution_revision >= 0",
            name="ck_agent_resume_claims_expected_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "consumed_execution_revision IS NULL OR consumed_execution_revision >= 0",
            name="ck_agent_resume_claims_consumed_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "(state = 'CONSUMED' AND consumed_at IS NOT NULL "
            "AND consumed_execution_revision IS NOT NULL) "
            "OR (state != 'CONSUMED' AND consumed_at IS NULL "
            "AND consumed_execution_revision IS NULL)",
            name="ck_agent_resume_claims_consumed_shape",
        ),
        sa.CheckConstraint(
            "(state = 'REJECTED' AND rejected_at IS NOT NULL) "
            "OR (state != 'REJECTED' AND rejected_at IS NULL)",
            name="ck_agent_resume_claims_rejected_shape",
        ),
        sa.CheckConstraint(
            "(state = 'EXPIRED' AND expired_at IS NOT NULL) "
            "OR (state != 'EXPIRED' AND expired_at IS NULL)",
            name="ck_agent_resume_claims_expired_shape",
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["agent_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["agent_execution_checkpoints.checkpoint_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("claim_id"),
        sa.UniqueConstraint(
            "resume_request_id",
            name="uq_agent_resume_claims_resume_request_id",
        ),
    )
    for name, columns in (
        ("ix_agent_resume_claims_execution_id", ["execution_id"]),
        ("ix_agent_resume_claims_checkpoint_id", ["checkpoint_id"]),
        ("ix_agent_resume_claims_user_id", ["user_id"]),
        ("ix_agent_resume_claims_client_id", ["client_id"]),
        ("ix_agent_resume_claims_connection_id", ["connection_id"]),
        ("ix_agent_resume_claims_state", ["state"]),
    ):
        op.create_index(name, "agent_resume_claims", columns)


def downgrade() -> None:
    op.drop_table("agent_resume_claims")
    op.drop_table("agent_checkpoint_pending_invocations")
    op.drop_table("agent_execution_checkpoints")

    with op.batch_alter_table("agent_tool_results") as batch_op:
        batch_op.drop_index("ix_agent_tool_results_commit_state")
        batch_op.drop_constraint(
            "ck_agent_tool_results_commit_state",
            type_="check",
        )
        batch_op.drop_column("commit_state")

    with op.batch_alter_table("agent_executions") as batch_op:
        batch_op.drop_index("ix_agent_executions_bound_connection_id")
        batch_op.drop_index("ix_agent_executions_bound_client_id")
        batch_op.drop_index("ix_agent_executions_current_checkpoint_id")
        batch_op.drop_column("bound_connection_id")
        batch_op.drop_column("bound_client_id")
        batch_op.drop_column("current_checkpoint_id")
