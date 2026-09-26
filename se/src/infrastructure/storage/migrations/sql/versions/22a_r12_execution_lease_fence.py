"""AE-R12-B durable execution owner/lease/fence representation.

Revision ID: 22a_r12_execution_lease_fence
Revises: 21a_ctx_f5_memory_foundation

Adds only durable ownership representation for AgentExecution. Lease
acquisition/renewal/release, stale-RUNNING scanning, recovery takeover,
runtime dispatch fencing, and invocation reconciliation remain out of scope.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "22a_r12_execution_lease_fence"
down_revision: Union[str, None] = "21a_ctx_f5_memory_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agent_executions") as batch:
        batch.add_column(
            sa.Column("owner_instance_id", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "lease_expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.add_column(
            sa.Column(
                "lease_generation",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.create_check_constraint(
            "ck_agent_executions_lease_owner_expiry_pair",
            "(owner_instance_id IS NULL AND lease_expires_at IS NULL) OR "
            "(owner_instance_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_agent_executions_lease_generation_nonnegative",
            "lease_generation >= 0",
        )
        batch.create_check_constraint(
            "ck_agent_executions_lease_owner_generation_positive",
            "owner_instance_id IS NULL OR lease_generation > 0",
        )


def downgrade() -> None:
    bind = op.get_bind()
    active_authority_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM agent_executions "
            "WHERE owner_instance_id IS NOT NULL "
            "OR lease_expires_at IS NOT NULL "
            "OR lease_generation != 0"
        )
    ).scalar_one()

    if active_authority_count:
        raise RuntimeError(
            "Cannot downgrade AE-R12-B while durable execution lease/fence "
            "authority is non-default."
        )

    with op.batch_alter_table("agent_executions") as batch:
        batch.drop_constraint(
            "ck_agent_executions_lease_owner_generation_positive",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_executions_lease_generation_nonnegative",
            type_="check",
        )
        batch.drop_constraint(
            "ck_agent_executions_lease_owner_expiry_pair",
            type_="check",
        )
        batch.drop_column("lease_generation")
        batch.drop_column("lease_expires_at")
        batch.drop_column("owner_instance_id")
