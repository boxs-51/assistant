from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    JSON,
    String,
    TIMESTAMP,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class FileBlobRecord(Base):
    """Physical canonical bytes stored behind an ObjectStorageDriver."""

    __tablename__ = "file_blobs"
    __table_args__ = (
        UniqueConstraint(
            "storage_backend",
            "bucket",
            "object_key",
            name="uq_file_blobs_storage_locator",
        ),
        CheckConstraint(
            "state IN ('STAGING', 'UNVERIFIED_LEGACY', 'READY', 'MISSING', "
            "'DELETING', 'DELETED', 'ERROR')",
            name="ck_file_blobs_state",
        ),
        CheckConstraint(
            "state != 'READY' OR "
            "(size_bytes IS NOT NULL AND size_bytes >= 0 AND sha256 IS NOT NULL)",
            name="ck_file_blobs_ready_integrity",
        ),
        Index(
            "ix_file_blobs_storage_locator",
            "storage_backend",
            "bucket",
            "object_key",
        ),
        Index("ix_file_blobs_state", "state"),
        Index("ix_file_blobs_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=default_uuid_str
    )
    storage_backend: Mapped[str] = mapped_column(String(64), nullable=False)
    bucket: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    object_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="STAGING", server_default="STAGING"
    )
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detected_mime_type: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    etag: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
