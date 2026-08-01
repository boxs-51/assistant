# src/runtimes/session/runtime.py
from typing import Dict, Any
import structlog

from ...kernel.base import BaseRuntime, RuntimeManifest
from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent

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
        self._sessions: Dict[str, Dict[str, Any]] = {}

    async def initialize(self, context: Dict[str, Any]) -> None:
        # Subscribe các event theo chuẩn tên mới
        self.event_bus = context.event_bus
        self.event_bus.subscribe("transport.event.request_received", self._on_request_received)
        self.event_bus.subscribe("provider.execution.succeeded", self._on_provider_responded)
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
        
        # Nạp trạng thái session/history từ StorageEngine...
        
        # Bắn Event báo hiệu Session đã load xong
        await self.event_bus.publish(BaseEvent(
            event_name="session.event.loaded",
            session_id=session_id,
            payload=event.payload
        ))

    async def _on_provider_responded(self, event: BaseEvent):
        """Lưu câu trả lời/lịch sử mới của LLM vào Memory hoặc Storage Engine."""
        logger.debug("Saving provider response to session memory", session_id=event.session_id)
        # Logic lưu trữ vào DB/Memory...
        pass