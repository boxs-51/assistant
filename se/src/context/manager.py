import json
from typing import Callable, Optional

import structlog
from sqlalchemy.orm import selectinload

from ..domain.schemas.attachment import GatewayAttachment
from ..domain.schemas.context import ContextObject, Project
from ..domain.schemas.identity import Identity
from ..domain.schemas.message import (
    GatewayMessage,
    decode_persisted_message_content,
)
from ..domain.schemas.request import GatewayChatRequest
from ..domain.schemas.session import Session as SessionSchema
from ..infrastructure.storage.core.manager import StorageEngine
from ..infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from ..infrastructure.storage.models.sql.chat_data.session import (
    Session as OrmSession,
)
from ..infrastructure.storage.repositories.chat_data.sessions import (
    SessionRepository,
)

logger = structlog.get_logger(__name__)


class ContextEngine:
    def __init__(
        self,
        storage_engine: StorageEngine,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
    ):
        self._storage = storage_engine
        self.uow_factory = uow_factory
        logger.info(
            "ContextEngine initialized, using UoW for long-term persistence."
        )

    @staticmethod
    def _timestamp(value) -> float:
        return (
            value.timestamp()
            if hasattr(value, "timestamp")
            else float(value)
        )

    @staticmethod
    def _legacy_attachment_schema(attachment) -> GatewayAttachment:
        metadata = attachment.metadata_json or {}
        return GatewayAttachment(
            id=attachment.id,
            filename=attachment.filename,
            mime_type=attachment.mime_type,
            size=attachment.size_bytes,
            uri=attachment.storage_uri,
            source="local",
            metadata=metadata,
        )

    @staticmethod
    def _asset_schema(file_record, blob_record) -> GatewayAttachment:
        metadata = dict(file_record.metadata_json or {})
        if blob_record.sha256:
            metadata["sha256"] = blob_record.sha256
        return GatewayAttachment(
            asset_id=file_record.id,
            filename=file_record.filename,
            mime_type=file_record.mime_type,
            size=blob_record.size_bytes,
            extension=file_record.extension,
            uri=f"asset://{file_record.id}",
            source="asset",
            metadata=metadata,
        )

    @classmethod
    def _session_schema(cls, session_db) -> SessionSchema:
        return SessionSchema(
            session_id=session_db.id,
            user_id=session_db.user_id,
            organization_id=session_db.organization_id,
            status=session_db.status,
            metadata=session_db.metadata_json or {},
            messages=[
                GatewayMessage(
                    role=message.role,
                    content=decode_persisted_message_content(
                        message.content
                    ),
                    turn_id=getattr(message, "turn_id", None),
                    sequence=(
                        getattr(message, "sequence", None)
                        if (getattr(message, "sequence", None) or 0) >= 1
                        else None
                    ),
                    created_at=getattr(message, "created_at", None),
                    completed_at=getattr(message, "completed_at", None),
                )
                for message in session_db.messages
            ],
            created_at=cls._timestamp(session_db.created_at),
            updated_at=cls._timestamp(session_db.updated_at),
        )

    async def load_context(
        self,
        session_id: str,
        identity: Identity,
    ) -> ContextObject:
        async with self.uow_factory() as uow:
            session_db = await uow.sessions.get_by_id(
                session_id,
                options=[
                    selectinload(OrmSession.messages),
                    selectinload(OrmSession.attachments),
                ],
            )
            if not session_db or session_db.user_id != identity.user_id:
                raise ValueError(
                    f"Session {session_id} not found or access denied."
                )

            project_db = None
            if session_db.project_id:
                project_db = await uow.projects.get_by_id(
                    session_db.project_id,
                    with_relations=True,
                )

            central_rows = await uow.assets.list_context_file_rows(
                session_id=session_id,
                project_id=session_db.project_id,
            )
            accessible_files: list[GatewayAttachment] = []
            seen: set[tuple[str, str]] = set()

            for file_record, blob_record in central_rows:
                item = self._asset_schema(file_record, blob_record)
                key = ("asset", file_record.id)
                if key not in seen:
                    accessible_files.append(item)
                    seen.add(key)

            # Legacy Attachment remains a read-only compatibility source until F8.
            legacy = []
            if project_db:
                legacy.extend(project_db.attachments)
            legacy.extend(session_db.attachments)
            for attachment in legacy:
                key = ("legacy", attachment.id)
                if key in seen:
                    continue
                accessible_files.append(
                    self._legacy_attachment_schema(attachment)
                )
                seen.add(key)

            session_schema = self._session_schema(session_db)
            project_schema = (
                Project(
                    project_id=project_db.id,
                    user_id=project_db.user_id,
                    organization_id=project_db.organization_id,
                    name=project_db.name,
                    created_at=self._timestamp(project_db.created_at),
                    updated_at=self._timestamp(project_db.updated_at),
                    files=accessible_files,
                )
                if project_db
                else None
            )
            return ContextObject(
                project=project_schema,
                session=session_schema,
                accessible_files=accessible_files,
            )

    async def create_new_session(
        self,
        identity: Identity,
        project_id: Optional[str] = None,
    ) -> SessionSchema:
        async with self.uow_factory() as uow:
            if project_id:
                project = await uow.projects.get_by_id(project_id)
                if not project or project.user_id != identity.user_id:
                    raise ValueError("Project not found or access denied.")

            new_session_db = await uow.sessions.create_session(
                user_id=identity.user_id,
                organization_id=identity.organization_id,
                project_id=project_id,
            )
            await uow.commit()
            return self._session_schema(new_session_db)

    async def summarize_session(
        self,
        session_id: str,
        model_router,
        http_client,
    ):
        logger.info(
            "Summarization process started for session",
            session_id=session_id,
        )
        async with self.uow_factory() as uow:
            session_repo: SessionRepository = uow.sessions
            messages_db = await session_repo.get_messages_by_session_id(
                session_id,
                limit=100,
            )
            if not messages_db:
                logger.warning(
                    "No messages found",
                    session_id=session_id,
                )
                return

            history_text = "\n".join(
                f"{msg.role}: {json.dumps(msg.content)}"
                for msg in messages_db
            )
            summary_prompt = (
                "Hãy tóm tắt ngắn gọn cuộc hội thoại sau đây "
                "trong khoảng 50 từ:\n\n"
                + history_text
            )
            request_body = GatewayChatRequest(
                model="gpt-4o-mini",
                messages=[
                    GatewayMessage(
                        role="user",
                        content=summary_prompt,
                    )
                ],
            ).model_dump(exclude_none=True)

            summary_response = await model_router.execute_with_fallback(
                http_client,
                request_body,
            )
            summary_text = summary_response.choices[0].message.content
            await session_repo.update_session_metadata(
                session_id,
                {"summary": summary_text},
            )
            await uow.commit()
            logger.info(
                "Session summary updated successfully",
                session_id=session_id,
            )
