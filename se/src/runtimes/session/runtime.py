# src/runtimes/session/runtime.py
from typing import Dict, Any
from datetime import datetime, timezone
import uuid
import structlog

from ...kernel.base import BaseRuntime, RuntimeContext, RuntimeManifest
from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent
from ...domain.schemas.identity import Identity
from ...application.messages import (
    CanonicalMessageService,
    MessageAccessDeniedError,
    MessagePersistenceError,
    NonCanonicalAssetContentError,
)

logger = structlog.get_logger(__name__)


class SessionRuntime(BaseRuntime):
    def __init__(self):
        manifest = RuntimeManifest(
            id="session_runtime",
            name="SessionRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None
        self.uow_factory = None
        self.message_service = None
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._stream_buffers: Dict[tuple[str, str], Dict[str, Any]] = {}
        self._subscribed = False

    async def initialize(self, context: RuntimeContext) -> None:
        await super().initialize(context)
        # Subscribe các event theo chuẩn tên mới
        self.event_bus = context.event_bus
        self.uow_factory = context.uow_factory
        self.message_service = (
            getattr(context.container, "message_service", None)
            or CanonicalMessageService(context.uow_factory)
        )
        if self.uow_factory is None:
            raise ValueError("SessionRuntime requires uow_factory.")
        if not self._subscribed:
            self.event_bus.subscribe("transport.event.request_received", self._on_request_received)
            self.event_bus.subscribe("provider.chat.responded", self._on_provider_responded)
            self.event_bus.subscribe("provider.stream.chunk_emitted", self._on_stream_chunk)
            self.event_bus.subscribe("provider.stream.completed", self._on_stream_completed)
            self._subscribed = True
        self._is_initialized = True
        logger.info("SessionRuntime initialized")

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False
        if self.event_bus is not None and self._subscribed:
            self.event_bus.unsubscribe("transport.event.request_received", self._on_request_received)
            self.event_bus.unsubscribe("provider.chat.responded", self._on_provider_responded)
            self.event_bus.unsubscribe("provider.stream.chunk_emitted", self._on_stream_chunk)
            self.event_bus.unsubscribe("provider.stream.completed", self._on_stream_completed)
            self._stream_buffers.clear()
            self._subscribed = False

    def _message_authority(self):
        if self.message_service is None:
            if self.uow_factory is None:
                raise RuntimeError(
                    "SessionRuntime message authority is unavailable."
                )
            self.message_service = CanonicalMessageService(self.uow_factory)
        return self.message_service

    async def _on_request_received(self, event: BaseEvent):
        session_id = event.session_id
        identity_data = event.payload.get("identity")
        if not session_id or not identity_data:
            return
        identity = (
            identity_data
            if isinstance(identity_data, Identity)
            else Identity.model_validate(identity_data)
        )
        turn_id = (
            event.turn_id
            or event.payload.get("turn_id")
            or f"turn_{uuid.uuid4().hex}"
        )
        request_body = event.payload.get("request_body", {})
        messages = request_body.get("messages", [])
        try:
            await self._message_authority().persist_request_messages(
                session_id=session_id,
                owner_user_id=identity.user_id,
                organization_id=identity.organization_id,
                messages=messages,
                turn_id=turn_id,
            )
        except MessageAccessDeniedError as exc:
            # Preserve the pre-F4 Session ownership-denial transport contract:
            # fail closed without publishing a provider event.
            logger.warning(
                "Session access denied",
                session_id=session_id,
                error=str(exc),
            )
            return
        except (MessagePersistenceError, ValueError) as exc:
            await self._publish_persistence_failure(event, exc, status_code=400)
            return

        async with self.uow_factory() as uow:
            session = await uow.sessions.get_by_id(session_id)
            messages_db = await uow.sessions.get_messages_by_session_id(
                session_id
            )
            session_payload = {
                "session": {
                    "session_id": session.id,
                    "user_id": session.user_id,
                    "organization_id": session.organization_id,
                    "messages": [
                        {
                            "role": message.role,
                            "content": message.content,
                            "turn_id": message.turn_id,
                            "sequence": message.sequence,
                            "created_at": message.created_at,
                            "completed_at": message.completed_at,
                        }
                        for message in messages_db
                    ],
                }
            }

        await self.event_bus.publish(
            BaseEvent(
                event_name="session.event.loaded",
                session_id=session_id,
                turn_id=turn_id,
                payload={
                    **event.payload,
                    "session_id": session_id,
                    **session_payload,
                },
            )
        )

    async def _publish_persistence_failure(
        self,
        event: BaseEvent,
        exc: Exception,
        *,
        status_code: int,
    ) -> None:
        logger.warning(
            "Canonical message persistence rejected request",
            session_id=event.session_id,
            error=str(exc),
        )
        await self.event_bus.publish(
            BaseEvent(
                event_name="provider.failed",
                session_id=event.session_id,
                turn_id=event.turn_id,
                payload={
                    "error": str(exc),
                    "error_code": type(exc).__name__,
                    "failure_domain": "MESSAGE_PERSISTENCE",
                    "retryable": False,
                    "status_code": status_code,
                },
            )
        )

    async def _on_provider_responded(self, event: BaseEvent):
        """Lưu câu trả lời/lịch sử mới của LLM vào Memory hoặc Storage Engine."""
        logger.debug("Saving provider response to session memory", session_id=event.session_id)
        response = event.payload.get("response", {})
        choices = response.get("choices", [])
        if not event.session_id or not choices:
            return

        message = choices[0].get("message", {})
        content = message.get("content")
        if content is None:
            return

        await self._persist_assistant_message(
            event.session_id,
            message.get("role", "assistant"),
            content,
            event.turn_id,
            completed_at=datetime.now(timezone.utc),
        )

    async def _on_stream_chunk(self, event: BaseEvent):
        if not event.session_id or not event.turn_id:
            return

        chunk = event.payload.get("chunk", {})
        choices = chunk.get("choices", []) if isinstance(chunk, dict) else []
        if not choices:
            return

        delta = choices[0].get("delta", {})
        content = delta.get("content") if isinstance(delta, dict) else None
        if content:
            key = (event.session_id, event.turn_id)
            buffer = self._stream_buffers.setdefault(
                key,
                {"chunks": [], "created_at": datetime.now(timezone.utc)},
            )
            buffer["chunks"].append(content)

    async def _on_stream_completed(self, event: BaseEvent):
        if not event.session_id or not event.turn_id:
            return

        key = (event.session_id, event.turn_id)
        buffer = self._stream_buffers.get(key)
        if not buffer:
            return
        content = "".join(buffer["chunks"])
        if content:
            await self._persist_assistant_message(
                event.session_id,
                "assistant",
                content,
                event.turn_id,
                created_at=buffer["created_at"],
                completed_at=datetime.now(timezone.utc),
            )
        # Remove only after content assembly/persistence succeeded. If either
        # fails, retain the buffer so a retry cannot silently lose the turn.
        self._stream_buffers.pop(key, None)

    async def _persist_assistant_message(
        self,
        session_id: str,
        role: str,
        content: Any,
        turn_id: str | None,
        *,
        created_at: datetime | None = None,
        completed_at: datetime | None = None,
    ):
        if not turn_id:
            logger.warning(
                "Ignoring assistant message without turn correlation",
                session_id=session_id,
            )
            return
        try:
            await self._message_authority().persist_message(
                session_id=session_id,
                role=role,
                content=content,
                turn_id=turn_id,
                created_at=created_at,
                completed_at=completed_at,
            )
        except NonCanonicalAssetContentError as exc:
            logger.warning(
                "Skipping non-canonical assistant media persistence",
                session_id=session_id,
                error=str(exc),
            )
        except MessagePersistenceError as exc:
            logger.error(
                "Assistant message persistence failed",
                session_id=session_id,
                error=str(exc),
            )
