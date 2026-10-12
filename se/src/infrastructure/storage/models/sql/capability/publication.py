from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


SKILL_PUBLICATION_SCHEMA = "skill_publications"
SKILL_PUBLICATION_TABLE = "capability_publications"
SKILL_PUBLICATION_VERSION_TABLE = "alembic_version_skill_publications"
SKILL_PUBLICATION_HEAD = "skillpub_0001"


class PublicationBase(DeclarativeBase):
    metadata = MetaData(schema=SKILL_PUBLICATION_SCHEMA)


class CapabilityPublicationRecord(PublicationBase):
    __tablename__ = SKILL_PUBLICATION_TABLE

    capability_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    capability_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_class: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    publisher_type: Mapped[str] = mapped_column(String(32), nullable=False)
    publisher_id: Mapped[str] = mapped_column(String(255), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_definition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "PublicationBase",
    "CapabilityPublicationRecord",
    "SKILL_PUBLICATION_SCHEMA",
    "SKILL_PUBLICATION_TABLE",
    "SKILL_PUBLICATION_VERSION_TABLE",
    "SKILL_PUBLICATION_HEAD",
]
