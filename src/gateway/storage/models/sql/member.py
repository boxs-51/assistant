from sqlalchemy import Column, String, ForeignKey, PrimaryKeyConstraint, Table, ForeignKeyConstraint
from sqlalchemy.orm import relationship, Mapped, mapped_column
from typing import List

from .base import Base

# Bảng trung gian cho quan hệ nhiều-nhiều giữa Member và Role
member_role_association = Table(
    'member_roles',
    Base.metadata,
    # Foreign keys trỏ đến composite primary key của bảng members
    Column('member_organization_id', String(255), primary_key=True),
    Column('member_user_id', String(255), primary_key=True),
    Column('role_id', String(255), ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    ForeignKeyConstraint(
        ['member_organization_id', 'member_user_id'],
        ['members.organization_id', 'members.user_id'],
        ondelete='CASCADE'
    )
)

class Member(Base):
    """
    Model đại diện cho một thành viên trong một tổ chức,
    liên kết một người dùng với một tổ chức và gán cho họ một vai trò.
    """
    __tablename__ = 'members'

    organization_id: Mapped[str] = mapped_column(String(255), ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)

    user: Mapped["User"] = relationship(back_populates="memberships")
    organization: Mapped["Organization"] = relationship(back_populates="members")
    
    roles: Mapped[List["Role"]] = relationship(secondary=member_role_association)