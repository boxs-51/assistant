# src/runtimes/context/runtime.py

from typing import Any, List
import structlog

from ...kernel.base import BaseRuntime, RuntimeManifest
from ...domain.schemas.event import BaseEvent
from ...domain.schemas.request import GatewayChatRequest
from ...domain.schemas.message import GatewayMessage
from ...domain.schemas.enums import MessageContentType

logger = structlog.get_logger(__name__)


class ContextRuntime(BaseRuntime):
    def __init__(self):
        manifest = RuntimeManifest(
            id="context_runtime",
            name="ContextRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None

    async def initialize(self, context: Any) -> None:
        # Hỗ trợ cả Dict hoặc Dependency Container Object
        self.event_bus = context.get("event_bus") if isinstance(context, dict) else getattr(context, "event_bus", None)
        if not self.event_bus:
            raise ValueError("ContextRuntime requires 'event_bus' in initialization context.")

        self.event_bus.subscribe("context.command.build", self._handle_build_context)
        self._is_initialized = True
        logger.info("ContextRuntime initialized successfully")

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False

    async def _handle_build_context(self, event: BaseEvent):
        """Xử lý Command yêu cầu dựng Prompt/Context từ Session/History và Request mới."""
        await self.event_bus.publish(BaseEvent(
            event_name="context.event.built",
            session_id=event.session_id,
            payload=event.payload
        ))