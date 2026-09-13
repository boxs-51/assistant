from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class AgentExecutionRecord(Base):
    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    parent_execution_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    request: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    context_state: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    transcript: Mapped[Optional[list[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    inference_request: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    inference_response: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
