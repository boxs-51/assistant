import structlog
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..interfaces.repository import BaseRepository
from ..interfaces.database import DatabaseDriver
from ..models.sql.organization import Organization

logger = structlog.get_logger(__name__)

class OrganizationRepository(BaseRepository):
    """
    Repository cho các thao tác trên đối tượng Organization.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, owner_id: str) -> Organization:
        """Tạo một tổ chức mới."""
        new_org = Organization(name=name, owner_id=owner_id)
        self.session.add(new_org)
        await self.session.flush()
        logger.info("New organization added to session", org_id=new_org.id, name=name, owner_id=owner_id)
        return new_org

    async def get_by_id(self, org_id: str) -> Optional[Organization]:
        """Lấy một tổ chức bằng ID."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_owner_id(self, owner_id: str) -> List[Organization]:
        """Lấy danh sách các tổ chức mà một người dùng sở hữu."""
        stmt = select(Organization).where(Organization.owner_id == owner_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()