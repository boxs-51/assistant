from typing import List, Dict, Set
from fastapi import WebSocket
import json
import structlog

logger = structlog.get_logger(__name__)

class WebSocketConnectionManager:
    """Quản lý tập trung các kết nối WebSocket đang hoạt động và các đăng ký sự kiện của chúng."""
    def __init__(self):
        # Thay đổi: Lưu trữ các đăng ký cho mỗi kết nối
        self.active_connections: Dict[WebSocket, Set[str]] = {}

    async def connect(self, websocket: WebSocket):
        """Chấp nhận và lưu trữ một kết nối WebSocket mới với danh sách đăng ký rỗng."""
        await websocket.accept()
        self.active_connections[websocket] = set()

    def disconnect(self, websocket: WebSocket):
        """Xóa một kết nối WebSocket đã đóng."""
        if websocket in self.active_connections:
            del self.active_connections[websocket]

    async def subscribe(self, websocket: WebSocket, event_name: str):
        """Đăng ký một client vào một loại sự kiện cụ thể."""
        if websocket in self.active_connections:
            self.active_connections[websocket].add(event_name)
            logger.debug("Client subscribed to event", event_name=event_name, client=websocket.client)

    async def unsubscribe(self, websocket: WebSocket, event_name: str):
        """Hủy đăng ký một client khỏi một loại sự kiện."""
        if websocket in self.active_connections and event_name in self.active_connections[websocket]:
            self.active_connections[websocket].remove(event_name)
            logger.debug("Client unsubscribed from event", event_name=event_name, client=websocket.client)

    async def send_to_subscribers(self, event_name: str, data: dict):
        """Gửi dữ liệu JSON đến các client đã đăng ký lắng nghe sự kiện này."""
        message = json.dumps(data)
        for connection, subscriptions in self.active_connections.items():
            if event_name in subscriptions:
                await connection.send_text(message)

    async def shutdown(self):
        """Đóng tất cả các kết nối WebSocket đang hoạt động."""
        logger.info(f"Closing {len(self.active_connections)} active WebSocket connections.")
        for connection in list(self.active_connections.keys()):
            await connection.close(code=1001) # 1001: Going Away
        self.active_connections.clear()
        logger.info("All WebSocket connections have been closed.")