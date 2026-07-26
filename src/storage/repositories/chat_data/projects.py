import structlog
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...interfaces.repository import BaseRepository
from ...models.sql.chat_data.project import Project

logger = structlog.get_logger(__name__)

class ProjectRepository(BaseRepository):
    """Repository cho các thao tác trên đối tượng Project."""
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, user_id: str, organization_id: str) -> Project:
        """Tạo một project mới."""
        new_project = Project(name=name, user_id=user_id, organization_id=organization_id)
        self.session.add(new_project)
        await self.session.flush()
        logger.info("New project added to session", project_id=new_project.id, name=name, user_id=user_id)
        return new_project

    async def get_by_id(self, project_id: str, with_relations: bool = False) -> Optional[Project]:
        """Lấy một project bằng ID, tùy chọn tải các session và attachment liên quan."""
        stmt = select(Project).where(Project.id == project_id)
        if with_relations:
            stmt = stmt.options(selectinload(Project.sessions), selectinload(Project.attachments))
        
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_id(self, user_id: str) -> List[Project]:
        """Lấy danh sách các project mà một người dùng sở hữu."""
        stmt = select(Project).where(Project.user_id == user_id).order_by(Project.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()