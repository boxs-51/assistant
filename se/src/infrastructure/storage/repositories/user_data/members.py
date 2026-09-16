import structlog
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...interfaces.repository import BaseRepository
from ...interfaces.database import DatabaseDriver
from ...models.sql.user_data.member import Member
from ...models.sql.user_data.permission import Role

logger = structlog.get_logger(__name__)

class MemberRepository(BaseRepository):
    """
    Repository cho các thao tác trên đối tượng Member.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, organization_id: str, user_id: str, role: str) -> Member:
        """Thêm một user vào một organization với một vai trò cụ thể."""
        role_record = await self.session.scalar(
            select(Role).where(
                Role.name == role,
                Role.organization_id == organization_id,
            )
        )
        if role_record is None:
            role_record = Role(
                name=role,
                organization_id=organization_id,
            )
            self.session.add(role_record)
            await self.session.flush()

        new_member = Member(
            organization_id=organization_id,
            user_id=user_id,
        )
        new_member.roles.append(role_record)
        self.session.add(new_member)
        await self.session.flush()
        logger.info("New member added to session", user_id=user_id, org_id=organization_id, role=role)
        return new_member