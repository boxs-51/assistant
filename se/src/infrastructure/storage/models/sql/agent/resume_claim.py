from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class AgentResumeClaimRecord(Base):
    __tablename__ = "agent_resume_claims"
    __table_args__ = (
        CheckConstraint(
            "state IN ('CREATED', 'CONSUMED', 'REJECTED', 'EXPIRED')",
            name="ck_agent_resume_claims_state",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_agent_resume_claims_revision_nonnegative",
        ),
        CheckConstraint(
            "expected_execution_revision >= 0",
            name="ck_agent_resume_claims_expected_revision_nonnegative",
        ),
        CheckConstraint(
            "consumed_execution_revision IS NULL "
            "OR consumed_execution_revision >= 0",
            name="ck_agent_resume_claims_consumed_revision_nonnegative",
        ),
        CheckConstraint(
            "(state = 'CONSUMED' AND consumed_at IS NOT NULL "
            "AND consumed_execution_revision IS NOT NULL) "
            "OR (state != 'CONSUMED' AND consumed_at IS NULL "
            "AND consumed_execution_revision IS NULL)",
            name="ck_agent_resume_claims_consumed_shape",
        ),
        CheckConstraint(
            "(state = 'REJECTED' AND rejected_at IS NOT NULL) "
            "OR (state != 'REJECTED' AND rejected_at IS NULL)",
            name="ck_agent_resume_claims_rejected_shape",
        ),
        CheckConstraint(
            "(state = 'EXPIRED' AND expired_at IS NOT NULL) "
            "OR (state != 'EXPIRED' AND expired_at IS NULL)",
            name="ck_agent_resume_claims_expired_shape",
        ),
        UniqueConstraint(
            "resume_request_id",
            name="uq_agent_resume_claims_resume_request_id",
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    execution_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("agent_execution_checkpoints.checkpoint_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_execution_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client_id: Mapped[str | None] = mapped_column(String(255), index=True)
    connection_id: Mapped[str | None] = mapped_column(String(255), index=True)
    wait_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="CREATED", server_default="CREATED", index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claim_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_execution_revision: Mapped[int | None] = mapped_column(Integer)
