from sqlalchemy import String, ForeignKey, func, TIMESTAMP
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import List
from datetime import datetime

from ..base import Base
from ..custom_types import default_uuid_str

class Application(Base):
    __tablename__ = 'applications'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="applications")
    api_keys: Mapped[List["APIKey"]] = relationship("APIKey", back_populates="application", cascade="all, delete-orphan")