"""CAS-F7-T durable tool-media projection reservation.

Revision ID: 27a_cas_f7_t_tool_media_projection
Revises: 26a_ubq2_dual_accounting_bridge
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "27a_cas_f7_t_tool_media_projection"
down_revision: Union[str, None] = "26a_ubq2_dual_accounting_bridge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cas_f7_t_tool_media_projections",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("source_result_id", sa.String(length=255), nullable=False),
        sa.Column("execution_id", sa.String(length=255), nullable=False),
        sa.Column("invocation_id", sa.String(length=255), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("capability_id", sa.String(length=255), nullable=False),
        sa.Column("capability_version", sa.String(length=64), nullable=False),
        sa.Column("media_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_contract_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("origin_id", sa.String(length=255), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="RESERVED",
        ),
        sa.Column("asset_id", sa.String(length=255), nullable=True),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["source_result_id"],
            ["agent_tool_results.id"],
            ondelete="RESTRICT",
            name="fk_cas_f7t_projection_source_result",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["files.id"],
            ondelete="RESTRICT",
            name="fk_cas_f7t_projection_asset",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_result_id",
            "invocation_id",
            "tool_call_id",
            "capability_id",
            "media_ordinal",
            name="uq_cas_f7t_tool_media_source",
        ),
        sa.UniqueConstraint(
            "origin_id",
            name="uq_cas_f7t_tool_media_origin",
        ),
        sa.CheckConstraint(
            "media_ordinal >= 0",
            name="ck_cas_f7t_tool_media_ordinal",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_cas_f7t_tool_media_revision",
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED', 'INGESTING', 'READY', 'AMBIGUOUS')",
            name="ck_cas_f7t_tool_media_state",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT COUNT(*) FROM cas_f7_t_tool_media_projections")
    ).scalar_one()
    if count:
        raise RuntimeError(
            "Cannot downgrade CAS-F7-T while durable tool-media projections exist."
        )
    op.drop_table("cas_f7_t_tool_media_projections")
