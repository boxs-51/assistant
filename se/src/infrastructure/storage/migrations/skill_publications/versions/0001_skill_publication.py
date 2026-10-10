"""Initial durable Skill publication authority.

Revision ID: skillpub_0001
Revises:
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from se.src.infrastructure.storage.models.sql.capability.publication import (
    SKILL_PUBLICATION_SCHEMA,
    SKILL_PUBLICATION_TABLE,
)


revision = "skillpub_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        SKILL_PUBLICATION_TABLE,
        sa.Column("capability_id", sa.String(length=255), primary_key=True),
        sa.Column("capability_kind", sa.String(length=32), nullable=False),
        sa.Column("origin_class", sa.String(length=64), nullable=False),
        sa.Column("origin_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("publisher_type", sa.String(length=32), nullable=False),
        sa.Column("publisher_id", sa.String(length=255), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=255), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("canonical_definition", sa.JSON(), nullable=True),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("payload_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema=SKILL_PUBLICATION_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table(SKILL_PUBLICATION_TABLE, schema=SKILL_PUBLICATION_SCHEMA)
