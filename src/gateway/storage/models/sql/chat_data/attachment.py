from sqlalchemy import String, ForeignKey, func, TIMESTAMP, JSON, Integer
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import Dict, Any
from datetime import datetime

from ..base import Base
from ..custom_types import default_uuid_str

class Attachment(Base):
    __tablename__ = 'attachments'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id', ondelete='CASCADE'), nullable=True, index=True)

    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=True)
    storage_uri: Mapped[str] = mapped_column(String(2048), nullable=False, comment="URI to the file in object storage (e.g., S3, MinIO)")
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="attachments")
    session: Mapped["Session"] = relationship("Session", back_populates="attachments")