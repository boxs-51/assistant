import structlog
from typing import List, Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from ....storage.models.sql.chat_data.attachment import Attachment

logger = structlog.get_logger(__name__)

class AttachmentRepository:
    """
    Lớp Repository để tương tác với bảng 'attachments' trong cơ sở dữ liệu.
    """
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def create(self, attachment_data: dict) -> Attachment:
        """Tạo một attachment mới."""
        attachment = Attachment(**attachment_data)
        self.db_session.add(attachment)
        await self.db_session.flush()
        await self.db_session.refresh(attachment)
        logger.info("Created new attachment", attachment_id=attachment.id, filename=attachment.filename)
        return attachment

    async def get_by_id(self, attachment_id: str) -> Optional[Attachment]:
        """Lấy một attachment bằng ID."""
        statement = select(Attachment).where(Attachment.id == attachment_id)
        result = await self.db_session.execute(statement)
        return result.scalar_one_or_none()

    async def list_by_session_id(self, session_id: str) -> List[Attachment]:
        """Lấy danh sách các attachment theo session_id."""
        statement = select(Attachment).where(Attachment.session_id == session_id).order_by(Attachment.created_at)
        result = await self.db_session.execute(statement)
        return list(result.scalars().all())

    async def list_by_project_id(self, project_id: str) -> List[Attachment]:
        """Lấy danh sách các attachment theo project_id."""
        statement = select(Attachment).where(Attachment.project_id == project_id).order_by(Attachment.created_at)
        result = await self.db_session.execute(statement)
        return list(result.scalars().all())

    async def delete(self, attachment_id: str) -> bool:
        """Xóa một attachment bằng ID."""
        statement = delete(Attachment).where(Attachment.id == attachment_id)
        result = await self.db_session.execute(statement)
        if result.rowcount > 0:
            logger.info("Deleted attachment", attachment_id=attachment_id)
            return True
        return False