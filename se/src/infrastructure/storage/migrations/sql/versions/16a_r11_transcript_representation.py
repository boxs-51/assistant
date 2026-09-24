"""Add dormant AE-R11 transcript representation storage.

Revision ID: 16a_r11_transcript_representation
Revises: 15b_r9_aggregate_admission
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "16a_r11_transcript_representation"
down_revision: Union[str, None] = "15b_r9_aggregate_admission"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_transcript_chunks",
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("canonical_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "message_count >= 0",
            name="ck_agent_transcript_chunks_message_count_nonnegative",
        ),
        sa.CheckConstraint(
            "canonical_bytes >= 0",
            name="ck_agent_transcript_chunks_canonical_bytes_nonnegative",
        ),
        sa.PrimaryKeyConstraint("chunk_id"),
    )

    op.create_table(
        "agent_transcript_payload_nodes",
        sa.Column("payload_root_ref", sa.String(length=64), nullable=False),
        sa.Column(
            "parent_payload_root_ref",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("chunk_id", sa.String(length=64), nullable=False),
        sa.Column("logical_message_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "logical_message_count >= 0",
            name="ck_agent_transcript_payload_nodes_count_nonnegative",
        ),
        sa.CheckConstraint(
            "parent_payload_root_ref IS NULL "
            "OR parent_payload_root_ref != payload_root_ref",
            name="ck_agent_transcript_payload_nodes_not_self_parent",
        ),
        sa.ForeignKeyConstraint(
            ["parent_payload_root_ref"],
            ["agent_transcript_payload_nodes.payload_root_ref"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["agent_transcript_chunks.chunk_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("payload_root_ref"),
    )
    op.create_table(
        "agent_transcript_representations",
        sa.Column("transcript_ref", sa.String(length=64), nullable=False),
        sa.Column("transcript_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("parent_transcript_ref", sa.String(length=64), nullable=True),
        sa.Column("parent_transcript_version", sa.Integer(), nullable=True),
        sa.Column("delta_depth", sa.Integer(), nullable=False),
        sa.Column("logical_message_count", sa.Integer(), nullable=False),
        sa.Column(
            "logical_transcript_fingerprint",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column("payload_root_ref", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "transcript_version >= 0",
            name="ck_agent_transcript_representations_version_nonnegative",
        ),
        sa.CheckConstraint(
            "kind IN ('FULL', 'DELTA')",
            name="ck_agent_transcript_representations_kind",
        ),
        sa.CheckConstraint(
            "delta_depth >= 0 AND delta_depth <= 9",
            name="ck_agent_transcript_representations_depth",
        ),
        sa.CheckConstraint(
            "logical_message_count >= 0",
            name="ck_agent_transcript_representations_count_nonnegative",
        ),
        sa.CheckConstraint(
            "("
            "kind = 'FULL' "
            "AND transcript_version = 0 "
            "AND parent_transcript_ref IS NULL "
            "AND parent_transcript_version IS NULL "
            "AND delta_depth = 0"
            ") OR ("
            "kind = 'DELTA' "
            "AND transcript_version > 0 "
            "AND parent_transcript_ref IS NOT NULL "
            "AND parent_transcript_version IS NOT NULL "
            "AND delta_depth > 0"
            ")",
            name="ck_agent_transcript_representations_shape",
        ),
        sa.CheckConstraint(
            "parent_transcript_ref IS NULL "
            "OR parent_transcript_ref != transcript_ref "
            "OR parent_transcript_version != transcript_version",
            name="ck_agent_transcript_representations_not_self_parent",
        ),
        sa.ForeignKeyConstraint(
            ["parent_transcript_ref", "parent_transcript_version"],
            [
                "agent_transcript_representations.transcript_ref",
                "agent_transcript_representations.transcript_version",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payload_root_ref"],
            ["agent_transcript_payload_nodes.payload_root_ref"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("transcript_ref", "transcript_version"),
    )


def downgrade() -> None:
    op.drop_table("agent_transcript_representations")
    op.drop_table("agent_transcript_payload_nodes")
    op.drop_table("agent_transcript_chunks")
