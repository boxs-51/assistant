from sqlalchemy import String, ForeignKey, func, TIMESTAMP, JSON, Integer, UniqueConstraint
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid

from ..base import Base
from ..custom_types import default_uuid_str

class Session(Base):
    __tablename__ = 'sessions'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    project_id: Mapped[str] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), nullable=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True, index=True)
    
    title: Mapped[str] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=True)
    next_message_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    messages: Mapped[List["Message"]] = relationship("Message", back_populates="session", cascade="all, delete-orphan", order_by="Message.sequence.asc()")
    attachments: Mapped[List["Attachment"]] = relationship("Attachment", back_populates="session", cascade="all, delete-orphan")
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="sessions")

class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_messages_session_sequence"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False) # 'user', 'assistant'
    content: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, comment="Lưu toàn bộ object GatewayMessage Pydantic")
    turn_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        default=lambda: f"legacy_{uuid.uuid4().hex}",
    )
    # Zero is reserved for compatibility with direct legacy ORM inserts. The
    # repository allocates canonical conversation messages from one upward.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    @property
    def timestamp(self) -> datetime:
        """Compatibility alias for clients that still read the legacy field."""
        return self.created_at

    # Relationship
    session: Mapped["Session"] = relationship("Session", back_populates="messages")
