"""AIC-owned identity reservation separated from a live owner binding."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..user_data.user import User  # noqa: F401 - register users metadata for isolated Agent create_all


class AgentInstanceReservationRecord(Base):
    """Opaque ID survives retirement without retaining an owner FK."""

    __tablename__ = "aic_instance_reservations"
    __table_args__ = (
        CheckConstraint("state IN ('ACTIVE', 'RETIRING', 'RETIRED')", name="ck_aic_reservation_state"),
        CheckConstraint("generation >= 1", name="ck_aic_reservation_generation"),
    )

    agent_instance_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AgentInstanceLiveRecord(Base):
    """Only this table holds durable owner and definition binding."""

    __tablename__ = "aic_instance_live"
    __table_args__ = (
        CheckConstraint("lifecycle_state IN ('ACTIVE', 'SUSPENDED')", name="ck_aic_live_state"),
        CheckConstraint("generation >= 1", name="ck_aic_live_generation"),
        Index("ix_aic_live_owner_state", "owner_user_id", "lifecycle_state"),
    )

    agent_instance_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("aic_instance_reservations.agent_instance_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    definition_id: Mapped[str] = mapped_column(String(255), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(255), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["AgentInstanceReservationRecord", "AgentInstanceLiveRecord"]
