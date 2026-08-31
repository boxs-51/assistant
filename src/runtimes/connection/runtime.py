# src/runtime/runtimes/connection/runtime.py
import asyncio
import structlog
from typing import Dict, Any, Optional

from ...kernel.base import BaseRuntime, RuntimeContext, RuntimeManifest
from .session import ConnectionRegistry
from ...infrastructure.event_bus.bus import EventBus
from ...domain.schemas.event import BaseEvent

logger = structlog.get_logger(__name__)


class ConnectionRuntime(BaseRuntime):
    """Runtime quản lý toàn bộ kết nối active (WebSocket, SSE, Transport Sessions)."""

    def __init__(self):
        manifest = RuntimeManifest(
            id="connection_runtime",
            name="ConnectionRuntime",
            version="1.0.0"
        )
        super().__init__(manifest=manifest)
        self.event_bus = None
        self.registry = ConnectionRegistry()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._subscribed = False

    async def initialize(self, context: RuntimeContext) -> None:
        await super().initialize(context)
        # Subscribe Command gửi tin nhắn tới Client
        self.event_bus = context.event_bus
        if not self._subscribed:
            self.event_bus.subscribe("connection.command.send", self._handle_send_command)
            self._subscribed = True
        self._is_initialized = True
        logger.info("Connection Runtime initialized.")

    async def start(self) -> None:
        self._is_running = True
        self._heartbeat_task = asyncio.create_task(self._monitor_heartbeats())
        logger.info("Connection Runtime started.")

    async def stop(self) -> None:
        self._is_running = False
        if self.event_bus is not None and self._subscribed:
            self.event_bus.unsubscribe("connection.command.send", self._handle_send_command)
            self._subscribed = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        logger.info("Connection Runtime stopped.")

    async def _handle_send_command(self, event: BaseEvent):
        """Lắng nghe Command gửi tin nhắn xuống client qua WebSocket."""
        success = await self.send_to_client(event.session_id, event.payload)
        if not success:
            logger.warning("Failed to deliver message: client socket not found", session_id=event.session_id)

    async def send_to_client(self, session_id: str, data: Dict[str, Any]) -> bool:
        socket = self.registry.get_socket(session_id)
        if socket:
            await socket.send_json(data)
            return True
        return False

    async def _monitor_heartbeats(self):
        while self._is_running:
            await asyncio.sleep(30)
            # Logic kiểm tra heartbeat...