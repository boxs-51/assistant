import structlog
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import inspect

from ....domain.schemas.event import BaseEvent

logger = structlog.get_logger(__name__)


class StorageEventFactory:
    """
    Tạo các sự kiện liên quan đến storage dựa trên các thay đổi trong session của SQLAlchemy.
    """

    @staticmethod
    async def create_events_from_session(session: AsyncSession) -> List[BaseEvent]:
        """
        Tạo danh sách các sự kiện từ các đối tượng mới, đã sửa đổi hoặc đã xóa trong session.
        """
        events: List[BaseEvent] = []
        await session.flush()

        for obj in session.new:
            entity_name = obj.__class__.__name__.lower()
            events.append(BaseEvent(
                event_name=f"storage.{entity_name}.created",
                payload={"id": getattr(obj, 'id', None)}
            ))

        for obj in session.dirty:
            entity_name = obj.__class__.__name__.lower()
            insp = inspect(obj)
            changes: Dict[str, Dict[str, Any]] = {}
            
            for attr in insp.attrs:
                history = attr.history
                if history.has_changes():
                    old_value = history.deleted[0] if history.deleted else None
                    new_value = history.added[0] if history.added else None
                    changes[attr.key] = {"old": old_value, "new": new_value}

            if changes: # Chỉ tạo sự kiện nếu có sự thay đổi thực sự
                events.append(BaseEvent(
                    event_name=f"storage.{entity_name}.updated",
                    payload={
                        "id": getattr(obj, 'id', None),
                        "changes": changes
                    }
                ))

        for obj in session.deleted:
            entity_name = obj.__class__.__name__.lower()
            events.append(BaseEvent(
                event_name=f"storage.{entity_name}.deleted",
                payload={"id": getattr(obj, 'id', None)}
            ))

        if events:
            logger.info("Created storage events from session", count=len(events))
        return events