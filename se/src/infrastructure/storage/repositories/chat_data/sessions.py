import structlog
from typing import Any, List, Optional
from sqlalchemy import delete, select, update
from datetime import datetime, timezone
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from ...interfaces.repository import BaseRepository
from ...interfaces.database import DatabaseDriver
from ...models.sql.chat_data.session import Session, Message
from ...models.sql.chat_data.attachment import Attachment
from ...models.sql.assets.reference import FileReferenceRecord
from ...models.sql.agent.execution import AgentExecutionRecord

logger = structlog.get_logger(__name__)


class SessionRepository(BaseRepository):
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
        new_session = Session(
            id=session_id,
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
            title=title,
        )
        self.session.add(new_session)
        await self.session.flush()
        logger.info(
            "New session created",
            session_id=new_session.id,
            user_id=user_id,
            project_id=project_id,
        )
        return new_session

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: Any,
        *,
        turn_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ) -> Message:
        sequence_result = await self.session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(next_message_sequence=Session.next_message_sequence + 1)
            .returning(Session.next_message_sequence)
        )
        sequence = sequence_result.scalar_one()
        new_message = Message(
            session_id=session_id,
            role=role,
            content=content,
            turn_id=turn_id or f"legacy_{uuid.uuid4().hex}",
            sequence=sequence,
            created_at=created_at or datetime.now(timezone.utc),
            completed_at=completed_at,
        )
        self.session.add(new_message)
        await self.session.flush()
        logger.debug(
            "New message added to session",
            message_id=new_message.id,
            session_id=session_id,
        )
        return new_message

    async def get_message(
        self,
        session_id: str,
        message_id: str,
    ) -> Optional[Message]:
        result = await self.session.execute(
            select(Message).where(
                Message.id == message_id,
                Message.session_id == session_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_messages_by_session_id(
        self,
        session_id: str,
        limit: int = 100,
    ) -> List[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_message_content(
        self,
        session_id: str,
        message_id: str,
        content: Any,
    ) -> Optional[Message]:
        message = await self.get_message(session_id, message_id)
        if message is None:
            return None
        message.content = content
        await self.session.flush()
        return message

    async def get_by_id(
        self,
        session_id: str,
        options: Optional[List] = None,
    ) -> Optional[Session]:
        stmt = select(Session).where(Session.id == session_id)
        if options:
            stmt = stmt.options(*options)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_user_id(
        self,
        user_id: str,
        limit: int = 100,
    ) -> List[Session]:
        result = await self.session.execute(
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

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

    async def delete_owned_session(
        self,
        session_id: str,
        owner_user_id: str,
    ) -> bool:
        owned = await self.session.execute(
            select(Session.id).where(
                Session.id == session_id,
                Session.user_id == owner_user_id,
            )
        )
        if owned.scalar_one_or_none() is None:
            return False

        live_execution = await self.session.execute(
            select(AgentExecutionRecord.id)
            .where(
                AgentExecutionRecord.session_id == session_id,
                AgentExecutionRecord.state.in_(("CREATED", "RUNNING", "WAITING")),
            )
            .limit(1)
        )
        if live_execution.scalar_one_or_none() is not None:
            raise RuntimeError(
                "Session has a live agent execution and cannot be deleted."
            )

        message_ids_result = await self.session.execute(
            select(Message.id).where(Message.session_id == session_id)
        )
        message_ids = list(message_ids_result.scalars().all())
        if message_ids:
            await self.session.execute(
                delete(FileReferenceRecord).where(
                    FileReferenceRecord.message_id.in_(message_ids)
                )
            )
        await self.session.execute(
            delete(FileReferenceRecord).where(
                FileReferenceRecord.reference_type == "SESSION_RESOURCE",
                FileReferenceRecord.session_id == session_id,
            )
        )

        await self.session.execute(
            delete(Message).where(Message.session_id == session_id)
        )
        await self.session.execute(
            delete(Attachment).where(Attachment.session_id == session_id)
        )
        await self.session.execute(
            delete(Session).where(
                Session.id == session_id,
                Session.user_id == owner_user_id,
            )
        )
        await self.session.flush()
        return True

    async def update_session_metadata(
        self,
        session_id: str,
        metadata_update: dict[str, Any],
    ):
        session = await self.get_by_id(session_id)
        if session:
            if session.metadata_json is None:
                session.metadata_json = {}
            session.metadata_json.update(metadata_update)
            await self.session.flush()
