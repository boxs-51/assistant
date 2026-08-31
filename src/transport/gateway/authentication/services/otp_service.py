import json
import structlog
from datetime import datetime, timezone
from typing import Optional, Callable

from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .....infrastructure.storage.interfaces.cache import CacheDriver
logger = structlog.get_logger(__name__)

class OTPStorageService:
    def __init__(self, cache_driver: CacheDriver, uow_factory: Callable[[], SqlAlchemyUnitOfWork]):
        self.cache_driver = cache_driver
        self.uow_factory = uow_factory
        self.otp_ttl = 300       # 5 phút hiệu lực cho mã OTP
        self.cooldown_ttl = 60   # 60 giây chờ tối thiểu giữa các lần bấm gửi lại

    async def save_pending_registration(self, email: str, user_data: dict, otp: str) -> None:
        """Lưu trữ thông tin đăng ký tạm và mã OTP kèm cơ chế dự phòng DB."""
        payload = {
            "user_data": user_data,
            "otp": otp,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        payload_str = json.dumps(payload)

        # 1. Thử lưu vào cache_driver
        try:
            if self.cache_driver:
                # Ghi dữ liệu đăng ký tạm
                await self.cache_driver.set(f"pending_reg:{email}", payload_str, self.otp_ttl)
                # Ghi khóa đếm ngược cooldown gửi lại
                await self.cache_driver.set(f"otp_cooldown:{email}", "active", self.cooldown_ttl)
                return
        except Exception as e:
            logger.error("Redis error during OTP save, falling back to Database", error=str(e))

        # 2. Fallback sang DB nếu cache_driver sập
        # Cần thiết kế bảng `pending_registrations` hoặc tận dụng trường JSON/Bảng phụ trong user_repo
        async with self.uow_factory() as uow:
            await uow.pending_registrations.create_or_update(
                email=email,
                payload=payload_str, 
                expires_in_seconds=self.otp_ttl
            )

    async def check_cooldown(self, email: str) -> int:
        """Kiểm tra thời gian cooldown còn lại. Trả về số giây còn lại (0 nếu đã hết)."""
        try:
            if self.cache_driver:
                ttl = await self.cache_driver.get_ttl(f"otp_cooldown:{email}")
                return max(0, ttl) if ttl else 0
        except Exception as e:
            logger.error("cache_driver error during cooldown check, falling back to DB check", error=str(e))
            
        # Fallback DB: tính dựa trên thời gian bản ghi cũ tồn tại trong DB
        async with self.uow_factory() as uow:
            return await uow.pending_registrations.get_remaining_cooldown(email, self.cooldown_ttl)

    async def verify_and_get_data(self, email: str, input_otp: str) -> Optional[dict]:
        """Xác thực mã OTP và lấy dữ liệu người dùng ra để tạo tài khoản."""
        raw_data = None

        # 1. Thử lấy từ cache_driver
        try:
            if self.cache_driver:
                raw_data = await self.cache_driver.get(f"pending_reg:{email}")
                if raw_data:
                    await self.cache_driver.delete(f"pending_reg:{email}") # Xóa ngay sau khi đọc (OTP dùng 1 lần)
        except Exception as e:
            logger.error("Redis error during OTP verification, falling back to DB", error=str(e))

        # 2. Nếu Redis sập hoặc không tìm thấy, thử tìm trong DB Fallback
        if not raw_data:
            async with self.uow_factory() as uow:
                pending_record = await uow.pending_registrations.get_and_delete(email)
                if pending_record:
                    raw_data = pending_record.payload


        if not raw_data:
            return None

        data = json.loads(raw_data)
        
        # Kiểm tra tính khớp của mã OTP
        if data["otp"] == input_otp:
            return data["user_data"]
            
        return None