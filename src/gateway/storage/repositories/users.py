import structlog
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..interfaces.repository import BaseRepository
from ..interfaces.database import DatabaseDriver
from ..models.sql.user import User
from ..models.sql.organization import Organization
from ..models.sql.member import Member
from ..models.sql.oauth_account import OAuthAccount

logger = structlog.get_logger(__name__)

class UserRepository(BaseRepository):
    """
    Repository chịu trách nhiệm cho các thao tác CRUD trên đối tượng User.
    """
    def __init__(self, db_driver: DatabaseDriver):
        self.db_driver = db_driver

    async def create(self, email: str, hashed_password: str) -> User:
        """Tạo một user mới trong database."""
        new_user = User(
            email=email,
            password_hash=hashed_password
        )
        async with self.db_driver.get_session() as session:
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)
            logger.info("New user created", user_id=new_user.id, email=new_user.email)
            return new_user

    async def get_by_email(self, email: str) -> Optional[User]:
        """Lấy một user bằng địa chỉ email."""
        async with self.db_driver.get_session() as session:
            stmt = select(User).where(User.email == email)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_by_id(self, user_id: str) -> Optional[User]:
        """Lấy một user bằng ID."""
        async with self.db_driver.get_session() as session:
            stmt = select(User).where(User.id == user_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def get_user_roles(self, user_id: str) -> List[str]:
        """Lấy danh sách các vai trò của một người dùng từ các tổ chức họ tham gia."""
        async with self.db_driver.get_session() as session:
            stmt = (
                select(Member.role)
                .where(Member.user_id == user_id)
                .distinct()
            )
            result = await session.execute(stmt)
            roles = [row[0] for row in result.all()]
            return roles

    async def get_organization_for_user(self, user_id: str) -> Optional[Organization]:
        """
        Lấy tổ chức đầu tiên mà người dùng là thành viên hoặc chủ sở hữu.
        Trong mô hình hiện tại, giả định mỗi user thuộc về một tổ chức chính.
        """
        async with self.db_driver.get_session() as session:
            stmt = select(Organization).where(Organization.owner_id == user_id)
            result = await session.execute(stmt)
            # Ưu tiên tổ chức mà họ sở hữu
            return result.scalar_one_or_none()

    async def is_linked_to_oauth(self, user_id: str) -> bool:
        """Kiểm tra xem một user đã liên kết với bất kỳ provider OAuth nào chưa."""
        async with self.db_driver.get_session() as session:
            stmt = select(OAuthAccount.id).where(OAuthAccount.user_id == user_id).limit(1)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None
        
    async def save_pending_registration_fallback(self, email: str, payload: str, expires_in_seconds: int) -> None:
        """
        Lưu thông tin đăng ký tạm thời vào Database khi không kết nối được Redis.
        Giả định bạn có bảng/mô hình SQL tên là PendingRegistration.
        """
        from datetime import datetime, timedelta, timezone
        from ..models.sql.pending_registration import PendingRegistration # Thay bằng đường dẫn model thật của bạn
        
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
        
        async with self.db_driver.get_session() as session:
            # Xóa bản ghi cũ nếu đã tồn tại yêu cầu đăng ký trước đó cho email này
            delete_stmt = select(PendingRegistration).where(PendingRegistration.email == email)
            existing = await session.execute(delete_stmt)
            existing_row = existing.scalar_one_or_none()
            if existing_row:
                await session.delete(existing_row)

            # Tạo bản ghi lưu trữ OTP tạm mới
            new_pending = PendingRegistration(
                email=email,
                payload=payload,
                expires_at=expires_at,
                created_at=datetime.now(timezone.utc)
            )
            session.add(new_pending)
            await session.commit()
            logger.info("Saved pending registration to database fallback", email=email)

    async def get_remaining_cooldown_fallback(self, email: str, cooldown_ttl: int) -> int:
        """
        Tính toán thời gian cooldown gửi lại OTP dựa trên bản ghi database.
        """
        from datetime import datetime, timezone
        from ..models.sql.pending_registration import PendingRegistration
        
        async with self.db_driver.get_session() as session:
            stmt = select(PendingRegistration).where(PendingRegistration.email == email)
            result = await session.execute(stmt)
            pending = result.scalar_one_or_none()
            
            if not pending:
                return 0
                
            # Cooldown tính dựa trên: thời gian tạo + thời gian cooldown cấu hình
            elapsed_time = (datetime.now(timezone.utc) - pending.created_at.replace(tzinfo=timezone.utc)).total_seconds()
            remaining = cooldown_ttl - int(elapsed_time)
            return max(0, remaining)

    async def get_and_delete_pending_registration_fallback(self, email: str) -> Optional[str]:
        """
        Lấy thông tin payload đăng ký tạm từ database và xóa ngay lập tức (OTP dùng 1 lần).
        Chỉ trả về nếu mã OTP/bản ghi chưa hết hạn.
        """
        from datetime import datetime, timezone
        from ..models.sql.pending_registration import PendingRegistration
        
        async with self.db_driver.get_session() as session:
            stmt = select(PendingRegistration).where(PendingRegistration.email == email)
            result = await session.execute(stmt)
            pending = result.scalar_one_or_none()
            
            if not pending:
                return None
                
            # Kiểm tra thời gian hết hạn của OTP trong DB
            if pending.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                await session.delete(pending)
                await session.commit()
                logger.warning("Pending registration expired in database fallback", email=email)
                return None
                
            payload = pending.payload
            
            # Xóa bản ghi (chống reuse mã OTP)
            await session.delete(pending)
            await session.commit()
            return payload

    # --- HÀM TẠO USER TỰ ĐỘNG TỪ LUỒNG OAUTH ---

    async def create_user_from_oauth(self, email: str, name: str, provider: str) -> User:
        """
        Tạo nhanh một tài khoản User từ OAuth (Google/GitHub).
        Vì login qua bên thứ 3 nên password_hash sẽ được gán chuỗi ngẫu nhiên vô hiệu hóa.
        """
        import secrets
        # Tạo password hash ngẫu nhiên mà không ai đăng nhập thủ công bằng form được
        dummy_password_hash = f"oauth_disabled_{secrets.token_hex(16)}"
        
        new_user = User(
            email=email,
            password_hash=dummy_password_hash,
            # name=name # Thêm trường name nếu model User của bạn có hỗ trợ trường này
        )
        
        async with self.db_driver.get_session() as session:
            session.add(new_user)
            await session.commit()
            await session.refresh(new_user)
            logger.info("New user created via OAuth provider", user_id=new_user.id, email=new_user.email, provider=provider)
            return new_user       
    