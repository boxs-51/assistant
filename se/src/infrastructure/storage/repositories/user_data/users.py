import structlog
from typing import Optional, List
from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession


from ...interfaces.repository import BaseRepository
from ...interfaces.database import DatabaseDriver
from ...models.sql.user_data.user import User
from ...models.sql.user_data.organization import Organization
from ...models.sql.user_data.member import Member
from ...models.sql.user_data.oauth_account import OAuthAccount

logger = structlog.get_logger(__name__)

class UserRepository(BaseRepository):
    """
    Repository chịu trách nhiệm cho các thao tác CRUD trên đối tượng User.
    """
    def __init__(self, session: AsyncSession):
        self.session = session
        # Thêm trường name vào hàm create
    async def create(self, email: str, hashed_password: str, name: Optional[str] = None, picture: Optional[str] = None) -> User:
        """Tạo một user mới trong database."""
        new_user = User(
            email=email,
            password_hash=hashed_password,
            name=name,
            picture=picture
        )
        self.session.add(new_user)
        await self.session.flush() # Flush để lấy ID và các giá trị default từ DB
        logger.info("New user added to session", user_id=new_user.id, email=new_user.email)
        return new_user

    async def get_by_email(self, email: str) -> Optional[User]:
        """Lấy một user bằng địa chỉ email."""
        stmt = select(User).where(User.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: str) -> Optional[User]:
        """Lấy một user bằng ID."""
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_roles(self, user_id: str) -> List[str]:
        """Lấy danh sách các vai trò của một người dùng từ các tổ chức họ tham gia."""
        stmt = (
            select(Member.role)
            .where(Member.user_id == user_id)
            .distinct()
        )
        result = await self.session.execute(stmt)
        roles = [row[0] for row in result.all()]
        return roles

    async def get_organization_for_user(self, user_id: str) -> Optional[Organization]:
        """
        Lấy tổ chức đầu tiên mà người dùng là thành viên hoặc chủ sở hữu.
        Trong mô hình hiện tại, giả định mỗi user thuộc về một tổ chức chính.
        """
        stmt = select(Organization).where(Organization.owner_id == user_id)
        result = await self.session.execute(stmt)
        # Ưu tiên tổ chức mà họ sở hữu
        return result.scalar_one_or_none()

    async def is_linked_to_oauth(self, user_id: str) -> bool:
        """Kiểm tra xem một user đã liên kết với bất kỳ provider OAuth nào chưa."""
        stmt = select(OAuthAccount.id).where(OAuthAccount.user_id == user_id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

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
        
        self.session.add(new_user)
        await self.session.flush()
        logger.info("New user via OAuth added to session", user_id=new_user.id, email=new_user.email, provider=provider)
        return new_user
    