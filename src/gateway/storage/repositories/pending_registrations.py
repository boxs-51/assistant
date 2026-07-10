import structlog
from typing import Optional
from sqlalchemy import select, delete
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from ..interfaces.repository import BaseRepository
from ..interfaces.database import DatabaseDriver
from ..models.sql.pending_registration import PendingRegistration

logger = structlog.get_logger(__name__)

class PendingRegistrationRepository(BaseRepository):
    """
    Repository chịu trách nhiệm cho các thao tác trên đối tượng PendingRegistration.
    Được tách ra từ UserRepository để tuân thủ Single Responsibility Principle.
    """
    def __init__(self, session: Session):
        self.session = session

    async def create_or_update(self, email: str, payload: str, expires_in_seconds: int) -> PendingRegistration:
        """
        Lưu thông tin đăng ký tạm thời vào Database.
        Nếu đã tồn tại, sẽ cập nhật thay vì tạo mới (UPSERT).
        """
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)

        # Tìm và xóa bản ghi cũ nếu có
        existing = await self.session.get(PendingRegistration, email)
        if existing:
            await self.session.delete(existing)
            await self.session.flush() # Đảm bảo lệnh delete được thực thi trước khi add

        # Tạo bản ghi mới
        new_pending = PendingRegistration(
            email=email,
            payload=payload,
            expires_at=expires_at,
            created_at=datetime.now(timezone.utc)
        )
        self.session.add(new_pending)
        await self.session.flush()
        logger.info("Saved pending registration to session", email=email)
        return new_pending

    async def get_by_email(self, email: str) -> Optional[PendingRegistration]:
        """Lấy một bản ghi đăng ký tạm bằng email."""
        return await self.session.get(PendingRegistration, email)

    async def get_remaining_cooldown(self, email: str, cooldown_ttl: int) -> int:
        """
        Tính toán thời gian cooldown gửi lại OTP dựa trên bản ghi database.
        """
        pending = await self.session.get(PendingRegistration, email)
        if not pending:
            return 0
            
        # Cooldown tính dựa trên: thời gian tạo + thời gian cooldown cấu hình
        elapsed_time = (datetime.now(timezone.utc) - pending.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        remaining = cooldown_ttl - int(elapsed_time)
        return max(0, remaining)

    async def get_and_delete(self, email: str) -> Optional[PendingRegistration]:
        """
        Lấy một bản ghi đăng ký tạm và xóa nó ngay lập tức (dùng một lần).
        Chỉ trả về nếu bản ghi chưa hết hạn.
        """
        pending = await self.session.get(PendingRegistration, email)
        
        if not pending:
            return None
            
        # Kiểm tra thời gian hết hạn
        if pending.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            await self.session.delete(pending)
            await self.session.flush()
            logger.warning("Pending registration expired in database", email=email)
            return None
            
        # Xóa bản ghi để chống sử dụng lại
        await self.session.delete(pending)
        await self.session.flush()
        
        return pending

    async def cleanup_expired(self):
        """Xóa tất cả các bản ghi đã hết hạn."""
        stmt = delete(PendingRegistration).where(PendingRegistration.expires_at < datetime.now(timezone.utc))
        await self.session.execute(stmt)
        await self.session.flush()