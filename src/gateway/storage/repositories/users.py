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