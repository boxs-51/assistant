# models/sql/pending_registration.py
from sqlalchemy import Column, String, DateTime, Text
from datetime import datetime, timezone
from ..base import Base  # Hoặc lớp Base chứa metadata của dự án bạn

class PendingRegistration(Base):
    __tablename__ = "pending_registrations"

    email = Column(String(255), primary_key=True, index=True)
    payload = Column(Text, nullable=False)  # Lưu trữ JSON chứa mật khẩu mã hóa & tên user
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)