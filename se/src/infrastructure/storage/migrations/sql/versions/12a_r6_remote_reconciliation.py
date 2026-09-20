"""Add R6 remote invocation reconciliation persistence contracts.

Revision ID: 12a_r6_remote_reconciliation
Revises: 11a_r5_task_budget
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "12a_r6_remote_reconciliation"
down_revision: Union[str, None] = "11a_r5_task_budget"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("capability_invocations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "capability_version",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "idempotency",
                sa.String(length=32),
                nullable=False,
                server_default="UNKNOWN",
            )
        )
        batch_op.add_column(
            sa.Column(
                "request_fingerprint",
                sa.String(length=64),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "owner_user_id",
                sa.String(length=255),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "origin_client_id",
                sa.String(length=255),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "remote_outcome_state",
                sa.String(length=32),
                nullable=True,
            )
        )
        batch_op.create_check_constraint(
            "ck_capability_invocations_idempotency",
            "idempotency IN "
            "('IDEMPOTENT', 'DEDUPLICATED', 'NON_IDEMPOTENT', 'UNKNOWN')",
        )
        batch_op.create_check_constraint(
            "ck_capability_invocations_remote_outcome_state",
            "remote_outcome_state IS NULL OR remote_outcome_state IN "
            "('NOT_DISPATCHED', 'IN_FLIGHT', 'OUTCOME_UNKNOWN', "
            "'TERMINAL_COMMITTED')",
        )
        batch_op.create_index(
            "ix_capability_invocations_owner_user_id",
            ["owner_user_id"],
        )
        batch_op.create_index(
            "ix_capability_invocations_origin_client_id",
            ["origin_client_id"],
        )
        batch_op.create_index(
            "ix_capability_invocations_remote_outcome_state",
            ["remote_outcome_state"],
        )


def downgrade() -> None:
    with op.batch_alter_table("capability_invocations") as batch_op:
        batch_op.drop_index(
            "ix_capability_invocations_remote_outcome_state"
        )
        batch_op.drop_index(
            "ix_capability_invocations_origin_client_id"
        )
        batch_op.drop_index(
            "ix_capability_invocations_owner_user_id"
        )
        batch_op.drop_constraint(
            "ck_capability_invocations_remote_outcome_state",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_capability_invocations_idempotency",
            type_="check",
        )
        batch_op.drop_column("remote_outcome_state")
        batch_op.drop_column("origin_client_id")
        batch_op.drop_column("owner_user_id")
        batch_op.drop_column("request_fingerprint")
        batch_op.drop_column("idempotency")
        batch_op.drop_column("capability_version")
