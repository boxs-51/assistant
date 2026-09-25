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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class FileProviderBindingRecord(Base):
    """Provider-specific representation of one canonical Assistant asset."""

    __tablename__ = "file_provider_bindings"
    __table_args__ = (
        UniqueConstraint(
            "provider_name",
            "provider_namespace",
            "provider_file_id",
            name="uq_file_provider_bindings_provider_identity",
        ),
        UniqueConstraint(
            "file_id",
            "provider_name",
            "provider_namespace",
            "live_claim_token",
            name="uq_file_provider_bindings_live_slot",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_file_provider_bindings_revision_nonnegative",
        ),
        CheckConstraint(
            "state IN ('PROCESSING', 'ACTIVE', 'UNKNOWN', 'EXPIRED', "
            "'DELETING', 'DELETED', 'ERROR')",
            name="ck_file_provider_bindings_state",
        ),
        CheckConstraint(
            "state != 'ACTIVE' OR provider_file_id IS NOT NULL",
            name="ck_file_provider_bindings_active_provider_identity",
        ),
        CheckConstraint(
            "(state NOT IN ('PROCESSING', 'ACTIVE', 'UNKNOWN') "
            "OR live_claim_token = 'LIVE') AND "
            "(state NOT IN ('EXPIRED', 'ERROR') "
            "OR live_claim_token IS NULL)",
            name="ck_file_provider_bindings_live_claim",
        ),
        Index(
            "ix_file_provider_bindings_file_provider",
            "file_id",
            "provider_name",
        ),
        Index("ix_file_provider_bindings_state", "state"),
        Index("ix_file_provider_bindings_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=default_uuid_str
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider_name: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_namespace: Mapped[str] = mapped_column(
        String(255), nullable=False, default="default", server_default="default"
    )
    provider_file_id: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    provider_uri: Mapped[Optional[str]] = mapped_column(
        String(2048), nullable=True
    )
    source_blob_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("file_blobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_sha256: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    live_claim_token: Mapped[Optional[str]] = mapped_column(
        String(16),
        nullable=True,
        default="LIVE",
        server_default="LIVE",
    )
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="PROCESSING",
        server_default="PROCESSING",
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
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
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
