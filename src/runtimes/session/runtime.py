# src/runtimes/session/runtime.py
from ...kernel.base import BaseRuntime
from ...kernel.event import Event, EventBus
from typing import Dict, Any

class SessionRuntime(BaseRuntime):
    def __init__(self, event_bus: EventBus):
        super().__init__(name="SessionRuntime")
        self.event_bus = event_bus
        self._sessions: Dict[str, Dict[str, Any]] = {}

    async def initialize(self, context: Dict[str, Any]) -> None:
        # Subscribe các event liên quan đến khởi tạo session hoặc kết thúc câu thoại
        self.event_bus.subscribe("RequestReceived", self._on_request_received)
        self.event_bus.subscribe("ProviderResponded", self._on_provider_responded)
        self._is_initialized = True

    async def start(self) -> None:
        self._is_running = True

    async def stop(self) -> None:
        self._is_running = False

    async def _on_request_received(self, event: Event):
        session_id = event.session_id
        # Nạp trạng thái session/history từ StorageEngine (nếu cần)
        # Bắn Event yêu cầu Workflow Runtime bắt đầu luồng xử lý
        await self.event_bus.publish(Event(
            event_name="SessionLoaded",
            session_id=session_id,
            payload=event.payload
        ))

    async def _on_provider_responded(self, event: Event):
        # Lưu câu trả lời mới vào Memory / Storage
        pass