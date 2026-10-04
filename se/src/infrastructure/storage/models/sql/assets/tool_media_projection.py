from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class ToolMediaAssetProjectionRecord(Base):
    """Durable at-most-once CAS projection for one committed tool-media item."""

    __tablename__ = "cas_f7_t_tool_media_projections"
    __table_args__ = (
        UniqueConstraint(
            "source_result_id",
            "invocation_id",
            "tool_call_id",
            "capability_id",
            "media_ordinal",
            name="uq_cas_f7t_tool_media_source",
        ),
        UniqueConstraint(
            "origin_id",
            name="uq_cas_f7t_tool_media_origin",
        ),
        CheckConstraint(
            "media_ordinal >= 0",
            name="ck_cas_f7t_tool_media_ordinal",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_cas_f7t_tool_media_revision",
        ),
        CheckConstraint(
            "state IN ('RESERVED', 'INGESTING', 'READY', 'AMBIGUOUS')",
            name="ck_cas_f7t_tool_media_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    source_result_id: Mapped[str] = mapped_column(
        ForeignKey("agent_tool_results.id", ondelete="RESTRICT"),
        nullable=False,
    )
    execution_id: Mapped[str] = mapped_column(String(255), nullable=False)
    invocation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(255), nullable=False)
    capability_version: Mapped[str] = mapped_column(String(64), nullable=False)
    media_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_contract_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    origin_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="RESERVED",
        server_default="RESERVED",
    )
    asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("files.id", ondelete="RESTRICT"),
        nullable=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
