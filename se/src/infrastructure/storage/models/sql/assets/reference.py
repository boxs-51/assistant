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


class FileReferenceRecord(Base):
    """Where a canonical asset is used; ownership remains on FileAssetRecord."""

    __tablename__ = "file_references"
    __table_args__ = (
        CheckConstraint(
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
        UniqueConstraint(
            "message_id",
            "content_part_index",
            name="uq_file_references_message_part",
        ),
        Index("ix_file_references_file_id", "file_id"),
        Index("ix_file_references_message_id", "message_id"),
        Index("ix_file_references_session_id", "session_id"),
        Index("ix_file_references_project_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=default_uuid_str
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
    )
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    content_part_index: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
