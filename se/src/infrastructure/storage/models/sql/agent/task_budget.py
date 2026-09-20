from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class TaskBudgetRecord(Base):
    __tablename__ = "agent_task_budgets"

    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="OPEN", server_default="OPEN", index=True
    )

    max_total_executions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_active_executions: Mapped[int] = mapped_column(Integer, nullable=False)
    max_active_branches: Mapped[int] = mapped_column(Integer, nullable=False)
    max_parallel_agents: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_inference_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_tokens: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    max_total_cost_usd: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 8), nullable=True
    )
    max_delegation_depth: Mapped[int] = mapped_column(Integer, nullable=False)

    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    deny_recursive_agent_cycle: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    used_executions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    active_executions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    active_branches: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    active_parallel_agents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    used_tool_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    used_inference_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    used_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    used_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0"), server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_task_budget_revision_nonnegative"),
        CheckConstraint(
            "state IN ('OPEN', 'CLOSED')",
            name="ck_task_budget_state",
        ),
        CheckConstraint(
            "max_total_executions > 0 "
            "AND max_active_executions > 0 "
            "AND max_active_branches > 0 "
            "AND max_parallel_agents > 0 "
            "AND max_total_tool_calls > 0 "
            "AND max_total_inference_calls > 0 "
            "AND max_delegation_depth > 0",
            name="ck_task_budget_required_limits_positive",
        ),
        CheckConstraint(
            "max_total_tokens IS NULL OR max_total_tokens > 0",
            name="ck_task_budget_optional_tokens_positive",
        ),
        CheckConstraint(
            "max_total_cost_usd IS NULL OR max_total_cost_usd > 0",
            name="ck_task_budget_optional_cost_positive",
        ),
        CheckConstraint(
            "used_executions >= 0 "
            "AND active_executions >= 0 "
            "AND active_branches >= 0 "
            "AND active_parallel_agents >= 0 "
            "AND used_tool_calls >= 0 "
            "AND used_inference_calls >= 0 "
            "AND used_tokens >= 0 "
            "AND used_cost_usd >= 0",
            name="ck_task_budget_counters_nonnegative",
        ),
        CheckConstraint(
            "active_executions <= used_executions "
            "AND used_executions <= max_total_executions "
            "AND active_executions <= max_active_executions",
            name="ck_task_budget_execution_bounds",
        ),
        CheckConstraint(
            "active_parallel_agents <= active_executions "
            "AND active_parallel_agents <= max_parallel_agents",
            name="ck_task_budget_parallel_agent_bounds",
        ),
        CheckConstraint(
            "active_branches <= max_active_branches",
            name="ck_task_budget_branch_bounds",
        ),
        CheckConstraint(
            "used_tool_calls <= max_total_tool_calls",
            name="ck_task_budget_tool_bounds",
        ),
        CheckConstraint(
            "used_inference_calls <= max_total_inference_calls",
            name="ck_task_budget_inference_bounds",
        ),
        CheckConstraint(
            "(state = 'OPEN' AND closed_at IS NULL) "
            "OR (state = 'CLOSED' AND closed_at IS NOT NULL)",
            name="ck_task_budget_closed_at_state",
        ),
    )


class TaskBudgetReservationRecord(Base):
    __tablename__ = "agent_task_budget_reservations"

    task_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    reservation_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ("
            "'NEW_EXECUTION', 'RESUME_EXECUTION', 'RELEASE_EXECUTION', "
            "'TOOL_CALL', 'INFERENCE', 'USAGE', "
            "'BRANCH', 'RELEASE_BRANCH'"
            ")",
            name="ck_task_budget_reservation_kind",
        ),
    )
