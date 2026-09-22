"""F1 Central Asset Storage expand-only schema.

Revision ID: f1a_central_asset_storage
Revises: 13b_r7_pending_snapshot

This migration intentionally does not backfill or drop the legacy
attachments table. Object-store I/O is forbidden inside Alembic.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a_central_asset_storage"
down_revision: Union[str, None] = "13b_r7_pending_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "file_blobs",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("storage_backend", sa.String(length=64), nullable=False),
        sa.Column("bucket", sa.String(length=255), nullable=True),
        sa.Column("object_key", sa.String(length=2048), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="STAGING",
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("detected_mime_type", sa.String(length=255), nullable=True),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "state IN ('STAGING', 'UNVERIFIED_LEGACY', 'READY', 'MISSING', "
            "'DELETING', 'DELETED', 'ERROR')",
            name="ck_file_blobs_state",
        ),
        sa.CheckConstraint(
            "state != 'READY' OR "
            "(size_bytes IS NOT NULL AND size_bytes >= 0 AND sha256 IS NOT NULL)",
            name="ck_file_blobs_ready_integrity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "storage_backend",
            "bucket",
            "object_key",
            name="uq_file_blobs_storage_locator",
        ),
    )
    op.create_index(
        "ix_file_blobs_storage_locator",
        "file_blobs",
        ["storage_backend", "bucket", "object_key"],
        unique=False,
    )
    op.create_index("ix_file_blobs_state", "file_blobs", ["state"], unique=False)
    op.create_index("ix_file_blobs_sha256", "file_blobs", ["sha256"], unique=False)

    op.create_table(
        "files",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.String(length=255), nullable=False),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("blob_id", sa.String(length=255), nullable=False),
        sa.Column("filename", sa.String(length=1024), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=64), nullable=True),
        sa.Column("origin_type", sa.String(length=32), nullable=False),
        sa.Column("origin_id", sa.String(length=255), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="STAGING",
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_files_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "state IN ('STAGING', 'READY', 'QUARANTINED', 'DELETING', "
            "'DELETED', 'ERROR')",
            name="ck_files_state",
        ),
        sa.CheckConstraint(
            "origin_type IN ('USER_UPLOAD', 'ASSISTANT', 'TOOL', "
            "'PROVIDER_IMPORT', 'SYSTEM_IMPORT', 'LEGACY_MIGRATION')",
            name="ck_files_origin_type",
        ),
        sa.ForeignKeyConstraint(
            ["blob_id"],
            ["file_blobs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_files_owner_state",
        "files",
        ["owner_user_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_files_organization_state",
        "files",
        ["organization_id", "state"],
        unique=False,
    )
    op.create_index("ix_files_blob_id", "files", ["blob_id"], unique=False)
    op.create_index("ix_files_created_at", "files", ["created_at"], unique=False)

    op.create_table(
        "file_references",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("file_id", sa.String(length=255), nullable=False),
        sa.Column("reference_type", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("project_id", sa.String(length=255), nullable=True),
        sa.Column("content_part_index", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "("
            "reference_type = 'MESSAGE_CONTENT' "
            "AND message_id IS NOT NULL "
            "AND session_id IS NULL "
            "AND project_id IS NULL "
            "AND content_part_index IS NOT NULL "
            "AND content_part_index >= 0"
            ") OR ("
            "reference_type = 'SESSION_RESOURCE' "
            "AND message_id IS NULL "
            "AND session_id IS NOT NULL "
            "AND project_id IS NULL "
            "AND content_part_index IS NULL"
            ") OR ("
            "reference_type = 'PROJECT_RESOURCE' "
            "AND message_id IS NULL "
            "AND session_id IS NULL "
            "AND project_id IS NOT NULL "
            "AND content_part_index IS NULL"
            ")",
            name="ck_file_references_exact_locator",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["files.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "content_part_index",
            name="uq_file_references_message_part",
        ),
    )
    op.create_index(
        "ix_file_references_file_id",
        "file_references",
        ["file_id"],
        unique=False,
    )
    op.create_index(
        "ix_file_references_message_id",
        "file_references",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        "ix_file_references_session_id",
        "file_references",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_file_references_project_id",
        "file_references",
        ["project_id"],
        unique=False,
    )

    op.create_table(
        "file_provider_bindings",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("file_id", sa.String(length=255), nullable=False),
        sa.Column("provider_name", sa.String(length=64), nullable=False),
        sa.Column(
            "provider_namespace",
            sa.String(length=255),
            nullable=False,
            server_default="default",
        ),
        sa.Column("provider_file_id", sa.String(length=1024), nullable=False),
        sa.Column("provider_uri", sa.String(length=2048), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default="PROCESSING",
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "last_verified_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_file_provider_bindings_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "state IN ('PROCESSING', 'ACTIVE', 'EXPIRED', 'DELETING', "
            "'DELETED', 'ERROR')",
            name="ck_file_provider_bindings_state",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"],
            ["files.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_name",
            "provider_namespace",
            "provider_file_id",
            name="uq_file_provider_bindings_provider_identity",
        ),
    )
    op.create_index(
        "ix_file_provider_bindings_file_provider",
        "file_provider_bindings",
        ["file_id", "provider_name"],
        unique=False,
    )
    op.create_index(
        "ix_file_provider_bindings_state",
        "file_provider_bindings",
        ["state"],
        unique=False,
    )
    op.create_index(
        "ix_file_provider_bindings_expiry",
        "file_provider_bindings",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_file_provider_bindings_expiry",
        table_name="file_provider_bindings",
    )
    op.drop_index(
        "ix_file_provider_bindings_state",
        table_name="file_provider_bindings",
    )
    op.drop_index(
        "ix_file_provider_bindings_file_provider",
        table_name="file_provider_bindings",
    )
    op.drop_table("file_provider_bindings")

    op.drop_index(
        "ix_file_references_project_id",
        table_name="file_references",
    )
    op.drop_index(
        "ix_file_references_session_id",
        table_name="file_references",
    )
    op.drop_index(
        "ix_file_references_message_id",
        table_name="file_references",
    )
    op.drop_index(
        "ix_file_references_file_id",
        table_name="file_references",
    )
    op.drop_table("file_references")

    op.drop_index("ix_files_created_at", table_name="files")
    op.drop_index("ix_files_blob_id", table_name="files")
    op.drop_index("ix_files_organization_state", table_name="files")
    op.drop_index("ix_files_owner_state", table_name="files")
    op.drop_table("files")

    op.drop_index("ix_file_blobs_sha256", table_name="file_blobs")
    op.drop_index("ix_file_blobs_state", table_name="file_blobs")
    op.drop_index("ix_file_blobs_storage_locator", table_name="file_blobs")
    op.drop_table("file_blobs")
