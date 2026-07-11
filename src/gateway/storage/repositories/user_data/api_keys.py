import structlog
from typing import Optional, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from ...interfaces.repository import BaseRepository
from ...interfaces.database import DatabaseDriver
from ...models.sql.user_data.api_key import APIKey
from ...models.sql.user_data.application import Application

logger = structlog.get_logger(__name__)

class APIKeyRepository(BaseRepository):
    """
    Repository chịu trách nhiệm cho các thao tác trên đối tượng APIKey.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, application_id: str, prefix: str, hashed_key: str) -> APIKey:
        """Tạo một API key mới trong database."""
        new_key = APIKey(
            application_id=application_id,
            prefix=prefix,
            hashed_key=hashed_key
        )
        self.session.add(new_key)
        await self.session.flush()
        logger.info("New API key added to session", key_id=new_key.id, prefix=new_key.prefix)
        return new_key

    async def get_by_prefix(self, prefix: str) -> Optional[APIKey]:
        """Lấy một API key bằng prefix (phần đầu của key, ví dụ: 'sk_live')."""
        stmt = (
            select(APIKey)
            .where(APIKey.prefix == prefix)
            .options(joinedload(APIKey.application).joinedload(Application.organization))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_application_ids(self, application_ids: List[str]) -> List[APIKey]:
        """Lấy danh sách các API key bằng danh sách các application ID."""
        if not application_ids:
            return []
        stmt = (
            select(APIKey)
            .where(APIKey.application_id.in_(application_ids))
            .options(selectinload(APIKey.application)) # Tải sẵn thông tin application để lấy tên
            .order_by(APIKey.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_id_and_owner(self, key_id: str, owner_id: str) -> Optional[APIKey]:
        """
        Lấy một API key bằng ID và xác thực nó thuộc về người dùng (owner).
        Điều này ngăn người dùng A xóa key của người dùng B.
        """
        stmt = (
            select(APIKey)
            .join(APIKey.application)
            .where(APIKey.id == key_id, Application.organization.has(owner_id=owner_id))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke(self, key_id: str) -> bool:
        """Thu hồi (vô hiệu hóa) một API key."""
        stmt = update(APIKey).where(APIKey.id == key_id).values(status="revoked")
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0