# src/runtimes/session/runtime.py
from typing import Dict, Any
import structlog

from ...kernel.base import BaseRuntime, RuntimeContext, RuntimeManifest
from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent
from ...domain.schemas.identity import Identity

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
        self._sessions: Dict[str, Dict[str, Any]] = {}

    async def initialize(self, context: RuntimeContext) -> None:
        # Subscribe các event theo chuẩn tên mới
        self.event_bus = context.event_bus
        self.uow_factory = context.uow_factory
        if self.uow_factory is None:
            raise ValueError("SessionRuntime requires uow_factory.")
        self.event_bus.subscribe("transport.event.request_received", self._on_request_received)
        self.event_bus.subscribe("provider.chat.responded", self._on_provider_responded)
        self._is_initialized = True
        logger.info("SessionRuntime initialized")

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False

    async def _on_request_received(self, event: BaseEvent):
        """Xử lý khi có request HTTP/WS mới vào hệ thống."""
        session_id = event.session_id
        logger.debug("Handling request received, loading session", session_id=session_id)
        
        identity_data = event.payload.get("identity")
        if not session_id or not identity_data:
            return

        identity = identity_data if isinstance(identity_data, Identity) else Identity.model_validate(identity_data)

        async with self.uow_factory() as uow:
            session = await uow.sessions.get_by_id(session_id)
            is_new_session = session is None
            if is_new_session:
                session = await uow.sessions.create_session(
                    user_id=identity.user_id,
                    organization_id=identity.organization_id,
                    session_id=session_id,
                )
                logger.info("Session created for request", session_id=session_id)
            elif session.user_id != identity.user_id:
                logger.warning("Session access denied", session_id=session_id)
                return

            request_body = event.payload.get("request_body", {})
            messages = request_body.get("messages", [])
            messages_to_persist = messages if is_new_session else messages[-1:]
            for message in messages_to_persist:
                await uow.sessions.add_message(
                    session_id=session_id,
                    role=message.get("role", "user"),
                    content={"type": "text", "data": message.get("content", "")},
                )
            await uow.commit()

            session = await uow.sessions.get_by_id(session_id)
            messages = await uow.sessions.get_messages_by_session_id(session_id)
            session_payload = {
                "session": {
                    "session_id": session.id,
                    "user_id": session.user_id,
                    "organization_id": session.organization_id,
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                }
            }
        
        # Bắn Event báo hiệu Session đã load xong
        await self.event_bus.publish(BaseEvent(
            event_name="session.event.loaded",
            session_id=session_id,
            payload={**event.payload, "session_id": session_id, **session_payload}
        ))

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

        async with self.uow_factory() as uow:
            await uow.sessions.add_message(
                session_id=event.session_id,
                role=message.get("role", "assistant"),
                content={"type": "text", "data": content},
            )
            await uow.commit()