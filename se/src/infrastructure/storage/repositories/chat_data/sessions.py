import structlog
from typing import Optional, List, Dict, Any
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...interfaces.repository import BaseRepository
from ...interfaces.database import DatabaseDriver
from ...models.sql.chat_data.session import Session, Message
from ...models.sql.chat_data.attachment import Attachment

logger = structlog.get_logger(__name__)

class SessionRepository(BaseRepository):
    """
    Repository cho các thao tác trên đối tượng Session và Message.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_session(
        self,
        user_id: str,
        organization_id: str,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        """Tạo một phiên hội thoại (session) mới."""
        new_session = Session(
            id=session_id,
            user_id=user_id, 
            organization_id=organization_id, 
            project_id=project_id,
            title=title
        )
        self.session.add(new_session)
        await self.session.flush()
        logger.info("New session created", session_id=new_session.id, user_id=user_id, project_id=project_id)
        return new_session

    async def add_message(self, session_id: str, role: str, content: Dict[str, Any]) -> Message:
        """Thêm một tin nhắn vào session."""
        new_message = Message(session_id=session_id, role=role, content=content)
        self.session.add(new_message)
        await self.session.flush()
        logger.debug("New message added to session", message_id=new_message.id, session_id=session_id)
        return new_message

    async def get_messages_by_session_id(self, session_id: str, limit: int = 100) -> List[Message]:
        """Lấy lịch sử tin nhắn của một session."""
        stmt = select(Message).where(Message.session_id == session_id).order_by(Message.timestamp.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_message_content(
        self, session_id: str, message_id: str, content: Any
    ) -> Optional[Message]:
        """Edit one message while preventing cross-session mutation."""
        stmt = select(Message).where(
            Message.id == message_id,
            Message.session_id == session_id,
        )
        result = await self.session.execute(stmt)
        message = result.scalar_one_or_none()
        if message is None:
            return None
        message.content = content if isinstance(content, dict) else {"type": "text", "data": content}
        await self.session.flush()
        return message

    async def get_by_id(self, session_id: str, options: Optional[List] = None) -> Optional[Session]:
        """Lấy một session bằng ID, có thể kèm theo các relations."""
        stmt = select(Session).where(Session.id == session_id)
        if options:
            stmt = stmt.options(*options)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user_id(self, user_id: str, limit: int = 100) -> List[Session]:
        stmt = select(Session).where(Session.user_id == user_id).order_by(Session.updated_at.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def claim_by_user_id(
        self,
        guest_user_id: str,
        target_user_id: str,
        target_organization_id: Optional[str],
    ) -> int:
        result = await self.session.execute(
            update(Session)
            .where(Session.user_id == guest_user_id)
            .values(
                user_id=target_user_id,
                organization_id=target_organization_id,
            )
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    async def delete_owned_session(self, session_id: str, owner_user_id: str) -> bool:
        owned = await self.session.execute(
            select(Session.id).where(
                Session.id == session_id,
                Session.user_id == owner_user_id,
            )
        )
        if owned.scalar_one_or_none() is None:
            return False

        # Explicit child deletion is portable even when SQLite FK cascades are disabled.
        await self.session.execute(delete(Message).where(Message.session_id == session_id))
        await self.session.execute(delete(Attachment).where(Attachment.session_id == session_id))
        await self.session.execute(
            delete(Session).where(
                Session.id == session_id,
                Session.user_id == owner_user_id,
            )
        )
        await self.session.flush()
        return True

    async def update_session_metadata(self, session_id: str, metadata_update: Dict[str, Any]):
        """Cập nhật (merge) trường metadata của một session."""
        session = await self.get_by_id(session_id)
        if session:
            if session.metadata is None: session.metadata = {}
            session.metadata.update(metadata_update)
            await self.session.flush()
