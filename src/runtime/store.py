import json
from typing import List, Dict, Any
from datetime import datetime
from ..domain.schemas.runtime.runtime import RuntimeEvent
from infrastructure.storage.interfaces.database import DatabaseDriver

class EventStore:
    """
    Lưu trữ append-only tất cả các sự kiện đã xảy ra của hệ thống.
    Cho phép khôi phục trạng thái (Event Sourcing) và tái hiện lỗi (Debugging/Replay).
    """
    def __init__(self, db_driver: DatabaseDriver):
        self.db = db_driver

    async def save(self, event: RuntimeEvent):
        """Ghi nhận sự kiện mới vào cơ sở dữ liệu bền vững."""
        query = """
            INSERT INTO event_store (
                event_id, event_type, timestamp, session_id, user_id, 
                correlation_id, causation_id, payload, metadata
            ) VALUES (:event_id, :event_type, :timestamp, :session_id, :user_id, 
                      :correlation_id, :causation_id, :payload, :metadata)
        """
        params = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "timestamp": event.timestamp,
            "session_id": event.session_id,
            "user_id": event.user_id,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "payload": json.dumps(event.payload),
            "metadata": json.dumps(event.metadata)
        }
        await self.db.execute_query(query, params)

    async def get_session_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Lấy toàn bộ dòng lịch sử sự kiện của một phiên để chuẩn bị Replay."""
        query = """
            SELECT * FROM event_store 
            WHERE session_id = :session_id 
            ORDER BY timestamp ASC
        """
        records = await self.db.execute_query(query, {"session_id": session_id})
        return records