from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class AgentToolCallRecord(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint("execution_id", "tool_call_id", name="uq_agent_tool_call_execution_call"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    execution_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    iteration_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("agent_iterations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invocation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tool_call_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    capability_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    arguments: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    extra_metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata", JSON, nullable=True, default=dict
    )
    retry_state: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
