"""CAS-F5-A durable provider-binding foundation.

Revision ID: 20a_cas_f5_binding_foundation
Revises: 19a_r11_query_order_indexes

Adds only the durable FileProviderBinding/config foundation released by
CAS-F5-0. Provider upload orchestration, remote deletion, UNKNOWN recovery,
READY asset deletion, and CAS physical GC remain out of scope.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20a_cas_f5_binding_foundation"
down_revision: Union[str, None] = "19a_r11_query_order_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("file_provider_bindings") as batch:
        batch.alter_column(
            "provider_file_id",
            existing_type=sa.String(length=1024),
            nullable=True,
        )
        batch.add_column(
            sa.Column("source_blob_id", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("source_sha256", sa.String(length=64), nullable=True)
        )
        batch.add_column(
            sa.Column("live_claim_token", sa.String(length=16), nullable=True)
        )
        batch.create_foreign_key(
            "fk_file_provider_bindings_source_blob_id_file_blobs",
            "file_blobs",
            ["source_blob_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.drop_constraint(
            "ck_file_provider_bindings_state",
            type_="check",
        )

    # Legacy schema had no runtime F5 caller. Preserve any rows while mapping
    # only states that are live under the released F5 contract.
    op.execute(
        sa.text(
            "UPDATE file_provider_bindings "
            "SET live_claim_token = 'LIVE' "
            "WHERE state IN ('PROCESSING', 'ACTIVE')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE file_provider_bindings "
            "SET live_claim_token = NULL "
            "WHERE state NOT IN ('PROCESSING', 'ACTIVE')"
        )
    )

    with op.batch_alter_table("file_provider_bindings") as batch:
        batch.alter_column(
            "live_claim_token",
            existing_type=sa.String(length=16),
            nullable=True,
            server_default="LIVE",
        )
        batch.create_check_constraint(
            "ck_file_provider_bindings_state",
            "state IN ('PROCESSING', 'ACTIVE', 'UNKNOWN', 'EXPIRED', "
            "'DELETING', 'DELETED', 'ERROR')",
        )
        batch.create_check_constraint(
            "ck_file_provider_bindings_active_provider_identity",
            "state != 'ACTIVE' OR provider_file_id IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_file_provider_bindings_live_claim",
            "(state NOT IN ('PROCESSING', 'ACTIVE', 'UNKNOWN') "
            "OR live_claim_token = 'LIVE') AND "
            "(state NOT IN ('EXPIRED', 'ERROR') "
            "OR live_claim_token IS NULL)",
        )
        batch.create_unique_constraint(
            "uq_file_provider_bindings_live_slot",
            [
                "file_id",
                "provider_name",
                "provider_namespace",
                "live_claim_token",
            ],
        )


def downgrade() -> None:
    bind = op.get_bind()
    unknown_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM file_provider_bindings "
            "WHERE state = 'UNKNOWN'"
        )
    ).scalar_one()
    missing_provider_identity = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM file_provider_bindings "
            "WHERE provider_file_id IS NULL"
        )
    ).scalar_one()

    if unknown_count:
        raise RuntimeError(
            "Cannot downgrade CAS-F5-A while UNKNOWN bindings exist."
        )
    if missing_provider_identity:
        raise RuntimeError(
            "Cannot downgrade CAS-F5-A while bindings lack provider_file_id."
        )

    with op.batch_alter_table("file_provider_bindings") as batch:
        batch.drop_constraint(
            "uq_file_provider_bindings_live_slot",
            type_="unique",
        )
        batch.drop_constraint(
            "ck_file_provider_bindings_live_claim",
            type_="check",
        )
        batch.drop_constraint(
            "ck_file_provider_bindings_active_provider_identity",
            type_="check",
        )
        batch.drop_constraint(
            "ck_file_provider_bindings_state",
            type_="check",
        )
        batch.drop_constraint(
            "fk_file_provider_bindings_source_blob_id_file_blobs",
            type_="foreignkey",
        )
        batch.create_check_constraint(
            "ck_file_provider_bindings_state",
            "state IN ('PROCESSING', 'ACTIVE', 'EXPIRED', 'DELETING', "
            "'DELETED', 'ERROR')",
        )
        batch.alter_column(
            "provider_file_id",
            existing_type=sa.String(length=1024),
            nullable=False,
        )
        batch.drop_column("live_claim_token")
        batch.drop_column("source_sha256")
        batch.drop_column("source_blob_id")
