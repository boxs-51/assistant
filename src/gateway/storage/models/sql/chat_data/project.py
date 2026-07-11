from sqlalchemy import String, ForeignKey, func, TIMESTAMP
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import List
from datetime import datetime

from ..base import Base
from ..custom_types import default_uuid_str

class Project(Base):
    __tablename__ = 'projects'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    sessions: Mapped[List["Session"]] = relationship("Session", back_populates="project", cascade="all, delete-orphan")
    attachments: Mapped[List["Attachment"]] = relationship("Attachment", back_populates="project", cascade="all, delete-orphan")
    owner: Mapped["User"] = relationship()
    organization: Mapped["Organization"] = relationship()

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name='{self.name}')>"