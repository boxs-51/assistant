# src/runtimes/workflow/runtime.py
from typing import Dict, Any
import structlog

from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent
from ...kernel.base import BaseRuntime, RuntimeManifest

logger = structlog.get_logger(__name__)


class WorkflowRuntime(BaseRuntime):
    def __init__(self):
        manifest = RuntimeManifest(
            id="workflow_runtime",
            name="WorkflowRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None

    async def initialize(self, context: Dict[str, Any]) -> None:
        self.event_bus = context.event_bus
        # Lắng nghe các Domain Event từ các Runtime khác
        self.event_bus.subscribe("session.event.loaded", self._handle_session_loaded)
        self.event_bus.subscribe("context.event.built", self._handle_context_built)
        self.event_bus.subscribe("capability.event.executed", self._handle_capability_executed)
        
        self._is_initialized = True
        logger.info("WorkflowRuntime initialized with updated event subscriptions")

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False

    async def _handle_session_loaded(self, event: BaseEvent):
        """Bước 1: Sau khi Session tải xong -> Yêu cầu Context Runtime xây dựng Prompt."""
        logger.debug("Handling session loaded, triggering context build", session_id=event.session_id)
        
        await self.event_bus.publish(BaseEvent(
            event_name="context.command.build",
            session_id=event.session_id,
            payload=event.payload
        ))

    async def _handle_context_built(self, event: BaseEvent):
        """Bước 2: Sau khi Context dựng xong -> Yêu cầu Provider Runtime gọi LLM."""
        logger.debug("Handling context built, triggering provider execution", session_id=event.session_id)
        
        await self.event_bus.publish(BaseEvent(
            event_name="provider.chat.execute",
            session_id=event.session_id,
            payload=event.payload
        ))

    async def _handle_capability_executed(self, event: BaseEvent):
        """Bước 3: Sau khi Capability/Tool chạy xong -> Gửi kết quả về Context Runtime để build lại Prompt."""
        logger.debug("Handling capability executed, re-triggering context build", session_id=event.session_id)
        
        await self.event_bus.publish(BaseEvent(
            event_name="context.command.build",
            session_id=event.session_id,
            payload={"tool_result": event.payload.get("result")}
        ))