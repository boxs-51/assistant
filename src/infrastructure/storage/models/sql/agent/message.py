from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class AgentMessageRecord(Base):
    __tablename__ = "agent_messages"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipient_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
