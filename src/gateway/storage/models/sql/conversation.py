from sqlalchemy import String, ForeignKey, func, TIMESTAMP, JSON
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import List, Dict, Any
from datetime import datetime
import uuid

from .base import Base

def default_uuid_str():
    return str(uuid.uuid4())

class Conversation(Base):
    __tablename__ = 'conversations'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    
    messages: Mapped[List["ChatMessage"]] = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")

class ChatMessage(Base):
    __tablename__ = 'chat_messages'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False) # 'user', 'assistant'
    content: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False) # Lưu toàn bộ object message Pydantic
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")