from datetime import datetime
from typing import List

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..custom_types import default_uuid_str


class AgentSessionRecord(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    owner_user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    agents: Mapped[List["AgentSessionMemberRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AgentSessionMemberRecord(Base):
    __tablename__ = "agent_session_members"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    session: Mapped[AgentSessionRecord] = relationship(back_populates="agents")
