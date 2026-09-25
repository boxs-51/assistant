"""CTX-F5-2 durable Memory persistence foundation.

Revision ID: 21a_ctx_f5_memory_foundation
Revises: 20a_cas_f5_binding_foundation

Adds only durable immutable Memory record persistence and exact admission/replay
constraints. Runtime promotion, retrieval/search, source hydration, lifecycle
mutation, ContextBuilder/model visibility, and deletion/GC remain out of scope.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "21a_ctx_f5_memory_foundation"
down_revision: Union[str, None] = "20a_cas_f5_binding_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("memory_id", sa.String(length=64), nullable=False),
        sa.Column(
            "promotion_authority_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("memory_schema_version", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "source_context_source_id",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("source_ref_json", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("canonical_bytes", sa.Integer(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "memory_schema_version >= 1",
            name="ck_memory_records_schema_version_positive",
        ),
        sa.CheckConstraint(
            "canonical_bytes >= 0",
            name="ck_memory_records_canonical_bytes_nonnegative",
        ),
        sa.PrimaryKeyConstraint("memory_id"),
        sa.UniqueConstraint(
            "promotion_authority_id",
            name="uq_memory_records_promotion_authority",
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_records")
