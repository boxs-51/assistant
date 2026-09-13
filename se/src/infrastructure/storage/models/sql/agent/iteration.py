from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class AgentIterationRecord(Base):
    __tablename__ = "agent_iterations"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    execution_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PREPARING")
    inference_request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    tool_call_ids: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True, default=list)
    transcript: Mapped[Optional[list[dict]]] = mapped_column(JSON, nullable=True)
    inference_request: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    inference_response: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
