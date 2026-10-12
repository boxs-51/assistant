"""Split ownerless non-reusable identity from an owner-bound LIVE record."""
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from ..base import Base
# Base.metadata.create_all is called by isolated AE/UBQ tests without the
# full user_data import aggregator. Register the FK target in that path.
from ..user_data.user import User  # noqa: F401


class AgentInstanceIdReservationRecord(Base):
    __tablename__ = "aic_instance_id_reservations"
    __table_args__ = (CheckConstraint("status IN ('LIVE','RETIRED')", name="ck_aic_reservation_state"),)
    agent_instance_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class AgentInstanceLiveRecord(Base):
    __tablename__ = "aic_instance_live"
    __table_args__ = (
        CheckConstraint("state IN ('ACTIVE','SUSPENDED')", name="ck_aic_live_state"),
        CheckConstraint("generation >= 1", name="ck_aic_live_generation"),
    )
    agent_instance_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("aic_instance_id_reservations.agent_instance_id", ondelete="RESTRICT"), primary_key=True
    )
    owner_user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    definition_id: Mapped[str] = mapped_column(String(255), nullable=False)
    definition_version: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
