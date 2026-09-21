"""Complete the R7 pending invocation semantic watermark.

Revision ID: 13b_r7_pending_snapshot
Revises: 13a_r7_durable_resume
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "13b_r7_pending_snapshot"
down_revision: Union[str, None] = "13a_r7_durable_resume"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_checkpoint_pending_invocations") as batch_op:
        batch_op.add_column(sa.Column("capability_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "idempotency",
                sa.String(length=32),
                nullable=False,
                server_default="UNKNOWN",
            )
        )
        batch_op.add_column(sa.Column("observed_remote_outcome_state", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("origin_client_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("origin_connection_id", sa.String(length=255), nullable=True))
        batch_op.create_check_constraint(
            "ck_agent_checkpoint_pending_invocations_idempotency",
            "idempotency IN ('IDEMPOTENT', 'DEDUPLICATED', 'NON_IDEMPOTENT', 'UNKNOWN')",
        )
        batch_op.create_check_constraint(
            "ck_agent_checkpoint_pending_invocations_remote_outcome",
            "observed_remote_outcome_state IS NULL OR "
            "observed_remote_outcome_state IN "
            "('NOT_DISPATCHED', 'IN_FLIGHT', 'OUTCOME_UNKNOWN', 'TERMINAL_COMMITTED')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_checkpoint_pending_invocations") as batch_op:
        batch_op.drop_constraint(
            "ck_agent_checkpoint_pending_invocations_remote_outcome",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_agent_checkpoint_pending_invocations_idempotency",
            type_="check",
        )
        batch_op.drop_column("origin_connection_id")
        batch_op.drop_column("origin_client_id")
        batch_op.drop_column("observed_remote_outcome_state")
        batch_op.drop_column("idempotency")
        batch_op.drop_column("request_fingerprint")
        batch_op.drop_column("capability_version")
