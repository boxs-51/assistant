from sqlalchemy import String, ForeignKey, func, TIMESTAMP
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import List
from datetime import datetime

from ..base import Base
from ..custom_types import default_uuid_str

class Organization(Base):
    __tablename__ = 'organizations'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, default='free', server_default='free')
    status: Mapped[str] = mapped_column(String(50), nullable=False, default='active', server_default='active')
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # Relationships
    owner: Mapped["User"] = relationship(back_populates="owned_organizations")
    members: Mapped[List["Member"]] = relationship("Member", back_populates="organization", cascade="all, delete-orphan")
    applications: Mapped[List["Application"]] = relationship("Application", back_populates="organization", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name='{self.name}')>"