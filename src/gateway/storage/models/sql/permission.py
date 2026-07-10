from sqlalchemy import String, ForeignKey, Table, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List, Optional
import uuid

from .base import Base

def default_uuid_str():
    return str(uuid.uuid4())

# Bảng trung gian cho quan hệ nhiều-nhiều giữa Role và Permission
role_permission_association = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id', String(255), ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    Column('permission_id', String(255), ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True)
)

class Permission(Base):
    __tablename__ = 'permissions'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True, comment="e.g., 'file:read', 'admin:write'")
    description: Mapped[Optional[str]] = mapped_column(String(1024))

    roles: Mapped[List["Role"]] = relationship(
        secondary=role_permission_association,
        back_populates='permissions'
    )

class Role(Base):
    __tablename__ = 'roles'

    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=default_uuid_str)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True, comment="NULL for system-wide roles")
    
    permissions: Mapped[List["Permission"]] = relationship(
        secondary=role_permission_association,
        back_populates='roles'
    )