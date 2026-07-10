import structlog
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..interfaces.repository import BaseRepository
from ..interfaces.database import DatabaseDriver
from ..models.sql.oauth_account import OAuthAccount
from ..models.sql.user import User

logger = structlog.get_logger(__name__)

class OAuthAccountRepository(BaseRepository):
    """
    Repository chịu trách nhiệm cho các thao tác trên đối tượng OAuthAccount.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: str, provider: str, provider_user_id: str) -> OAuthAccount:
        """Tạo một liên kết OAuth mới cho người dùng."""
        new_link = OAuthAccount(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id
        )
        self.session.add(new_link)
        await self.session.flush()
        logger.info("New OAuth account link added to session", user_id=user_id, provider=provider)
        return new_link

    async def get_by_provider_user_id(self, provider: str, provider_user_id: str) -> Optional[OAuthAccount]:
        """Tìm một liên kết OAuth bằng provider và provider_user_id."""
        stmt = select(OAuthAccount).where(
            OAuthAccount.provider == provider,
            OAuthAccount.provider_user_id == provider_user_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()