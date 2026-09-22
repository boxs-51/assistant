from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    TIMESTAMP,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class FileAssetRecord(Base):
    """Stable logical file identity owned by one user."""

    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_files_revision_nonnegative"),
        CheckConstraint(
            "state IN ('STAGING', 'READY', 'QUARANTINED', 'DELETING', "
            "'DELETED', 'ERROR')",
            name="ck_files_state",
        ),
        CheckConstraint(
            "origin_type IN ('USER_UPLOAD', 'ASSISTANT', 'TOOL', "
            "'PROVIDER_IMPORT', 'SYSTEM_IMPORT', 'LEGACY_MIGRATION')",
            name="ck_files_origin_type",
        ),
        Index("ix_files_owner_state", "owner_user_id", "state"),
        Index("ix_files_organization_state", "organization_id", "state"),
        Index("ix_files_blob_id", "blob_id"),
        Index("ix_files_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=default_uuid_str
    )
    owner_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    organization_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
    )
    blob_id: Mapped[str] = mapped_column(
        ForeignKey("file_blobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    origin_type: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="STAGING", server_default="STAGING"
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
