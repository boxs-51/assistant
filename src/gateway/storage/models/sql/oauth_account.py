import uuid
from sqlalchemy import Column, String, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.sql.sqltypes import TIMESTAMP

from .base import Base
from .custom_types import CUID

class OAuthAccount(Base):
    """
    Model đại diện cho một liên kết tài khoản OAuth của người dùng.
    Mỗi dòng trong bảng này thể hiện một người dùng đã liên kết tài khoản của họ
    với một nhà cung cấp bên ngoài (vd: Google, GitHub).
    """
    __tablename__ = 'oauth_accounts'

    id = Column(CUID, primary_key=True, default=lambda: f"oauth_{uuid.uuid4().hex}")
    user_id = Column(CUID, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    provider = Column(String(50), nullable=False, comment="Nhà cung cấp OAuth, vd: 'google', 'github'")
    provider_user_id = Column(String(255), nullable=False, comment="ID của người dùng trên hệ thống của nhà cung cấp")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    user_id: Mapped[int] = Column(ForeignKey("users.id", ondelete="CASCADE"))

    user: Mapped["User"] = relationship("User", back_populates="oauth_accounts")

    __table_args__ = (UniqueConstraint('provider', 'provider_user_id', name='uq_provider_user_id'),)