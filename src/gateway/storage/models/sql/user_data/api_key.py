from sqlalchemy import String, TIMESTAMP, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from ..base import Base
from ..custom_types import default_uuid_str

class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete='CASCADE'), nullable=False, index=True)
    
    prefix: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, index=True)
    hashed_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # Relationship
    application: Mapped["Application"] = relationship("Application", back_populates="api_keys")

    def __repr__(self) -> str:
        return f"<APIKey(id={self.id}, prefix='{self.prefix}', status='{self.status}')>"