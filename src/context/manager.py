import uuid
import time
from typing import Optional, Callable
import structlog
import json
from sqlalchemy.orm import selectinload

from ..domain.schemas.session import Session, SessionStatus
from ..domain.schemas.context import ContextObject, Project, GatewayAttachment
from ..domain.schemas.request import GatewayChatRequest, GatewayMessage
from ..domain.schemas.identity import Identity

from ..infrastructure.storage.core.manager import StorageEngine
from ..infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from ..infrastructure.storage.repositories.chat_data.projects import ProjectRepository
from ..infrastructure.storage.repositories.chat_data.sessions import SessionRepository

logger = structlog.get_logger(__name__)

class ContextEngine:
    """
    Quản lý ngữ cảnh runtime.
    Chịu trách nhiệm tải (load) Project, Session, và các tài nguyên liên quan
    từ hệ thống lưu trữ dài hạn (StorageEngine) để tạo ra một ContextObject
    cho Agent sử dụng tại thời điểm thực thi.
    """
    def __init__(self, storage_engine: StorageEngine, uow_factory: Callable[[], SqlAlchemyUnitOfWork]):
        self._storage = storage_engine
        self.uow_factory = uow_factory
        logger.info("ContextEngine initialized, using UoW for long-term persistence.")

    async def load_context(self, session_id: str, identity: Identity) -> ContextObject:
        """
        Tải toàn bộ ngữ cảnh cho một session cụ thể.
        Đây là hàm cốt lõi của Context Runtime.
        """
        async with self.uow_factory() as uow:
            # 1. Tải session từ DB, kèm theo các message và attachment liên quan
            session_repo = uow.repositories.get(SessionRepository)
            session_db = await session_repo.get_by_id(
                session_id, 
                options=[selectinload(Session.messages), selectinload(Session.attachments)]
            )
            if not session_db or session_db.user_id != identity.user_id:
                raise ValueError(f"Session {session_id} not found or access denied.")

            # 2. Tải project chứa session đó (nếu có)
            project_db = None
            if session_db.project_id:
                project_repo = uow.repositories.get(ProjectRepository)
                project_db = await project_repo.get_by_id(session_db.project_id, with_relations=True)

            # 3. Tập hợp các file có thể truy cập
            accessible_files = []
            if project_db:
                accessible_files.extend([GatewayAttachment.model_validate(f) for f in project_db.attachments])
            accessible_files.extend([GatewayAttachment.model_validate(f) for f in session_db.attachments])

            # 4. Chuyển đổi từ DB model sang Pydantic schema
            session_schema = Session.model_validate(session_db)
            project_schema = Project.model_validate(project_db) if project_db else None

            return ContextObject(project=project_schema, session=session_schema, accessible_files=accessible_files)

    async def create_new_session(self, identity: Identity, project_id: Optional[str] = None) -> Session:
        """Tạo một session mới và lưu vào DB."""
        async with self.uow_factory() as uow:
            session_repo = uow.repositories.get(SessionRepository)
            
            # Xác thực project_id nếu có
            if project_id:
                project_repo = uow.repositories.get(ProjectRepository)
                project = await project_repo.get_by_id(project_id)
                if not project or project.user_id != identity.user_id:
                    raise ValueError("Project not found or access denied.")

            new_session_db = await session_repo.create_session(
                user_id=identity.user_id,
                organization_id=identity.organization_id,
                project_id=project_id
            )
            await uow.commit()
            return Session.model_validate(new_session_db)

    async def summarize_session(self, session_id: str, model_router, http_client):
        """
        Thực hiện tóm tắt một session.
        1. Lấy lịch sử chat từ DB.
        2. Gọi LLM để tóm tắt.
        3. Lưu bản tóm tắt vào metadata của session.
        """
        logger.info("Summarization process started for session", session_id=session_id)
        
        async with self.uow_factory() as uow:
            session_repo: SessionRepository = uow.sessions
            messages_db = await session_repo.get_messages_by_session_id(session_id, limit=100)
            if not messages_db:
                logger.warning("No messages found to summarize", session_id=session_id)
                return

            history_text = "\n".join([f"{msg.role}: {json.dumps(msg.content)}" for msg in messages_db])
            
            summary_prompt = f"Hãy tóm tắt ngắn gọn cuộc hội thoại sau đây trong khoảng 50 từ:\n\n{history_text}"
            
            request_body = GatewayChatRequest(
                model="gpt-4o-mini", # Hoặc một model nhỏ, rẻ tiền
                messages=[GatewayMessage(role="user", content=summary_prompt)]
            ).model_dump(exclude_none=True)

            summary_response = await model_router.execute_with_fallback(http_client, request_body)
            summary_text = summary_response.choices[0].message.content

            await session_repo.update_session_metadata(session_id, {"summary": summary_text})
            await uow.commit()
            logger.info("Session summary updated successfully", session_id=session_id, summary=summary_text)