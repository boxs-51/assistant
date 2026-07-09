import structlog
from typing import Optional, List
from sqlalchemy import select

from ..interfaces.repository import BaseRepository
from ..interfaces.database import DatabaseDriver
from ..models.sql.member import Member

logger = structlog.get_logger(__name__)

class MemberRepository(BaseRepository):
    """
    Repository cho các thao tác trên đối tượng Member.
    """
    def __init__(self, db_driver: DatabaseDriver):
        self.db_driver = db_driver

    async def create(self, organization_id: str, user_id: str, role: str) -> Member:
        """Thêm một user vào một organization với một vai trò cụ thể."""
        new_member = Member(
            organization_id=organization_id,
            user_id=user_id,
            role=role
        )
        async with self.db_driver.get_session() as session:
            session.add(new_member)
            await session.commit()
            await session.refresh(new_member)
            logger.info("New member added to organization", user_id=user_id, org_id=organization_id, role=role)
            return new_member