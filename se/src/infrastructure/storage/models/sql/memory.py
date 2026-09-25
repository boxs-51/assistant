from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import CheckConstraint, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class MemoryRecordRow(Base):
    """Durable CTX-F5-2 representation of one immutable Memory record."""

    __tablename__ = "memory_records"
    __table_args__ = (
        UniqueConstraint(
            "promotion_authority_id",
            name="uq_memory_records_promotion_authority",
        ),
        CheckConstraint(
            "memory_schema_version >= 1",
            name="ck_memory_records_schema_version_positive",
        ),
        CheckConstraint(
            "canonical_bytes >= 0",
            name="ck_memory_records_canonical_bytes_nonnegative",
        ),
    )

    memory_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    promotion_authority_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    memory_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_context_source_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    source_ref_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
