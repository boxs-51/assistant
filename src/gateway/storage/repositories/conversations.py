import structlog
from typing import Optional, List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..interfaces.repository import BaseRepository
from ..interfaces.database import DatabaseDriver
from ..models.sql.conversation import Conversation, ChatMessage

logger = structlog.get_logger(__name__)

class ConversationRepository(BaseRepository):
    """
    Repository cho các thao tác trên đối tượng Conversation và ChatMessage.
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_conversation(self, user_id: str, organization_id: str, title: Optional[str] = None) -> Conversation:
        """Tạo một cuộc hội thoại mới."""
        new_convo = Conversation(user_id=user_id, organization_id=organization_id, title=title)
        self.session.add(new_convo)
        await self.session.flush()
        return new_convo

    async def add_message(self, conversation_id: str, role: str, content: Dict[str, Any]) -> ChatMessage:
        """Thêm một tin nhắn vào cuộc hội thoại."""
        new_message = ChatMessage(conversation_id=conversation_id, role=role, content=content)
        self.session.add(new_message)
        await self.session.flush()
        return new_message

    async def get_messages_by_conversation_id(self, conversation_id: str, limit: int = 100) -> List[ChatMessage]:
        """Lấy lịch sử tin nhắn của một cuộc hội thoại."""
        stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.timestamp.asc()).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()