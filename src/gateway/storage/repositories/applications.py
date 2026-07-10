import structlog
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..interfaces.repository import BaseRepository
from ..interfaces.database import DatabaseDriver
from ..models.sql.application import Application

logger = structlog.get_logger(__name__)

class ApplicationRepository(BaseRepository):
    """
    Repository cho các thao tác trên đối tượng Application.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, organization_id: str) -> Application:
        """Tạo một ứng dụng mới trong một tổ chức."""
        new_app = Application(name=name, organization_id=organization_id)
        self.session.add(new_app)
        await self.session.flush()
        logger.info("New application added to session", app_id=new_app.id, name=name, org_id=organization_id)
        return new_app

    async def get_by_id(self, app_id: str) -> Optional[Application]:
        """Lấy một ứng dụng bằng ID."""
        stmt = select(Application).where(Application.id == app_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_organization_id(self, organization_id: str) -> List[Application]:
        """Lấy danh sách các ứng dụng thuộc một tổ chức."""
        stmt = select(Application).where(Application.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()