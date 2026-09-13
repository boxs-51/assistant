from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class AgentToolResultRecord(Base):
    __tablename__ = "agent_tool_results"
    __table_args__ = (
        UniqueConstraint("execution_id", "tool_call_id", name="uq_agent_tool_result_execution_call"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    execution_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    iteration_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_iterations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    invocation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    output: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JSON, nullable=True, default=dict
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
