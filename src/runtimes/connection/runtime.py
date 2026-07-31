# src/runtime/runtimes/connection/runtime.py
import asyncio
import structlog
from typing import Dict, Any, Optional
from ...kernel.base import BaseRuntime
from .session import ConnectionRegistry

logger = structlog.get_logger(__name__)

class ConnectionRuntime(BaseRuntime):
    """Runtime quản lý toàn bộ kết nối active (WebSocket, SSE, Transport Sessions)."""

    def __init__(self):
        super().__init__(name="ConnectionRuntime")
        self.registry = ConnectionRegistry()
        self._heartbeat_task: Optional[asyncio.Task] = None

    async def initialize(self, context: Dict[str, Any]) -> None:
        self._is_initialized = True
        logger.info("Connection Runtime initialized.")

    async def start(self) -> None:
        self._is_running = True
        # Chạy ngầm task kiểm tra Ping/Pong (Heartbeat) định kỳ
        self._heartbeat_task = asyncio.create_task(self._monitor_heartbeats())
        logger.info("Connection Runtime started.")

    async def stop(self) -> None:
        self._is_running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        logger.info("Connection Runtime stopped.")

    async def send_to_client(self, session_id: str, data: Dict[str, Any]) -> bool:
        """API bắn dữ liệu realtime xuống client tương ứng qua WebSocket."""
        socket = self.registry.get_socket(session_id)
        if socket:
            await socket.send_json(data)
            return True
        return False

    async def _monitor_heartbeats(self):
        """Task dọn dẹp connection hỏng/timeout."""
        while self._is_running:
            await asyncio.sleep(30)
            # Logic quét kiểm tra last_ping bị quá hạn
            # Dọn dẹp connection hết hạn...