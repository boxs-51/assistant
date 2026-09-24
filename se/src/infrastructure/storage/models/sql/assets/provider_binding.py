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
        CheckConstraint(
            "revision >= 0",
            name="ck_file_provider_bindings_revision_nonnegative",
        ),
        CheckConstraint(
            "state IN ('PROCESSING', 'ACTIVE', 'EXPIRED', 'DELETING', "
            "'DELETED', 'ERROR')",
            name="ck_file_provider_bindings_state",
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
    provider_file_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    provider_uri: Mapped[Optional[str]] = mapped_column(
        String(2048), nullable=True
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
