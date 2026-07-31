# src/runtimes/workflow/runtime.py
from ...kernel.base import BaseRuntime, Event, EventBus
from typing import Dict, Any

class WorkflowRuntime(BaseRuntime):
    def __init__(self, event_bus: EventBus):
        super().__init__(name="WorkflowRuntime")
        self.event_bus = event_bus

    async def initialize(self, context: Dict[str, Any]) -> None:
        self.event_bus.subscribe("SessionLoaded", self._handle_session_loaded)
        self.event_bus.subscribe("ContextBuilt", self._handle_context_built)
        self.event_bus.subscribe("CapabilityExecuted", self._handle_capability_executed)
        self._is_initialized = True

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False

    async def _handle_session_loaded(self, event: Event):
        # Bước 1: Yêu cầu Context Runtime xây dựng Prompt
        await self.event_bus.publish(Event(
            event_name="BuildContext",
            session_id=event.session_id,
            payload=event.payload
        ))

    async def _handle_context_built(self, event: Event):
        # Bước 2: Yêu cầu Provider Runtime gọi LLM
        await self.event_bus.publish(Event(
            event_name="ExecuteProvider",
            session_id=event.session_id,
            payload=event.payload
        ))

    async def _handle_capability_executed(self, event: Event):
        # Bước 3: Sau khi Tool/Capability chạy xong, gửi lại kết quả cho LLM
        await self.event_bus.publish(Event(
            event_name="BuildContext",
            session_id=event.session_id,
            payload={"tool_result": event.payload["result"]}
        ))