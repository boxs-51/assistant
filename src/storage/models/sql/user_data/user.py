from sqlalchemy import String, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import List, Optional


from ..base import Base
from ..custom_types import default_uuid_str

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    picture: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True) # URL ảnh đại diện
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # Mối quan hệ tới bảng members để lấy vai trò
    memberships: Mapped[List["Member"]] = relationship("Member", back_populates="user", cascade="all, delete-orphan")
    owned_organizations: Mapped[List["Organization"]] = relationship("Organization", back_populates="owner")
    oauth_accounts: Mapped[list["OAuthAccount"]] = relationship(
        "OAuthAccount", 
        back_populates="user", 
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}')>"