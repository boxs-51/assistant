from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..custom_types import default_uuid_str


class AgentExecutionRecord(Base):
    __tablename__ = "agent_executions"
    __table_args__ = (
        CheckConstraint(
            "(owner_instance_id IS NULL AND lease_expires_at IS NULL) OR "
            "(owner_instance_id IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_agent_executions_lease_owner_expiry_pair",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="ck_agent_executions_lease_generation_nonnegative",
        ),
        CheckConstraint(
            "owner_instance_id IS NULL OR lease_generation > 0",
            name="ck_agent_executions_lease_owner_generation_positive",
        ),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    branch_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    parent_execution_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    retry_of_execution_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    base_execution_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    base_checkpoint_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    wait_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_instance_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    current_checkpoint_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    bound_client_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    bound_connection_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    remaining_active_budget_seconds: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    wait_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    request: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    context_state: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    transcript: Mapped[Optional[list[Dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    inference_request: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    inference_response: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())