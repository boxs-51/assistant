#src\infrastructure\event_bus\registry.py
from collections import defaultdict
import structlog
from typing import Callable, List, Dict, Awaitable, Any

from ...domain.schemas.event import BaseEvent

logger = structlog.get_logger(__name__)

EventHandler = Callable[[BaseEvent], Awaitable[None]]

class EventRegistry:
    """
    Quản lý tập trung việc đăng ký và truy xuất các hàm xử lý sự kiện (Event Handlers).
    Đây là nơi "khai báo" mối quan hệ giữa một tên sự kiện và các hàm sẽ lắng nghe nó.
    """
    def __init__(self):
        # Thay đổi: Lưu trữ cả handler và event_name mà nó đăng ký
        self._handlers_by_name: Dict[str, List[EventHandler]] = defaultdict(list)
        self._all_event_handlers: List[EventHandler] = []

    def register(self, event_name: str, handler: EventHandler):
        """Đăng ký một hàm xử lý cho một sự kiện cụ thể."""
        self._handlers_by_name[event_name].append(handler)
        logger.debug("New event handler registered", event_name=event_name, handler_name=handler.__name__)

    def unregister(self, event_name: str, handler: EventHandler):
        """Hủy đăng ký một hàm xử lý khỏi một sự kiện cụ thể."""
        if event_name in self._handlers_by_name:
            try:
                self._handlers_by_name[event_name].remove(handler)
                logger.debug("Event handler unregistered", event_name=event_name, handler_name=handler.__name__)
            except ValueError:
                logger.warning("Handler not found for event", event_name=event_name, handler_name=handler.__name__)
                
    def subscribe(self, event_name: str) -> Callable[[EventHandler], EventHandler]:
        """
        Decorator để đăng ký một hàm xử lý cho một sự kiện.
        Cách dùng:
        @registry.subscribe("user.created")
        async def handle_user_created(event: BaseEvent):
            ...
        """
        def decorator(handler: EventHandler) -> EventHandler:
            self.register(event_name, handler)
            return handler
        return decorator
    
    def unsubscribe(self, event_name: str, handler: EventHandler) -> None:
        self.unregister(event_name, handler)

    def register_for_all(self, handler: EventHandler):
        """Đăng ký một hàm xử lý sẽ được gọi cho mọi sự kiện được phát ra."""
        self._all_event_handlers.append(handler)
        logger.debug("New global event handler registered", handler_name=handler.__name__)

    def subscribe_to_all(self) -> Callable[[EventHandler], EventHandler]:
        """Decorator để đăng ký một hàm xử lý cho tất cả các sự kiện."""
        def decorator(handler: EventHandler) -> EventHandler:
            self.register_for_all(handler)
            return handler
        return decorator

    def get_handlers(self, event_name: str) -> List[EventHandler]:
        """Lấy danh sách các hàm xử lý đã đăng ký cho một sự kiện."""
        # Trả về cả handler cụ thể và handler toàn cục
        return self._handlers_by_name.get(event_name, []) + self._all_event_handlers