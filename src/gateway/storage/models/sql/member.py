from sqlalchemy import Column, String, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.orm import relationship, Mapped, mapped_column

from .base import Base

class Member(Base):
    """
    Model đại diện cho một thành viên trong một tổ chức,
    liên kết một người dùng với một tổ chức và gán cho họ một vai trò.
    """
    __tablename__ = 'members'

    organization_id: Mapped[str] = mapped_column(String(255), ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False) # e.g., 'admin', 'member'

    user: Mapped["User"] = relationship(back_populates="memberships")
    organization: Mapped["Organization"] = relationship(back_populates="members")