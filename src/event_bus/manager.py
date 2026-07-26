import structlog
import asyncio
from typing import Type, Any

from ..context.manager import ContextEngine
from ..storage.core.manager import StorageEngine
from ..storage.repositories.sessions import SessionRepository
from ..storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .bus import EventBus, EventDispatcher, EventPriority
from .registry import EventRegistry
from .ws_manager import WebSocketConnectionManager

logger = structlog.get_logger(__name__)

class EventingManager:
    """
    Lớp quản lý chịu trách nhiệm khởi tạo và cấu hình toàn bộ hệ thống sự kiện.
    Nó cũng đóng vai trò là một Dependency Container cho các event handler.
    """
    def __init__(self, storage_engine: StorageEngine, context_engine: ContextEngine):
        self.storage = storage_engine
        self.context_engine = context_engine

        # Tạo một "nhà máy" Unit of Work để cung cấp cho Dispatcher
        db_driver = self.storage.drivers.get("sqlite") # Hoặc postgres
        self.uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver, self.bus)
        
        # Định nghĩa bản đồ ưu tiên cho các sự kiện
        priority_map = {
            "tool.execution.requested": EventPriority.HIGH,
            "tool.execution.completed": EventPriority.HIGH,
            "system.event.failed": EventPriority.HIGH, # DLQ events cần được xử lý nhanh
            "user.created": EventPriority.NORMAL,
            "chat.session.started": EventPriority.NORMAL,
            # Các sự kiện broadcast hoặc metrics có thể có độ ưu tiên thấp
        }
        
        self.bus = EventBus(priority_map=priority_map)
        self.registry = EventRegistry() # Registry được dùng bởi dispatcher và subscribers
        self.dispatcher = EventDispatcher(
            registry=self.registry,
            queue=self.bus.queue,
            dependency_container=self, # Tự inject chính nó làm container
            cache_driver=self.storage.drivers.get("cache"),
            uow_factory=self.uow_factory
        )
        self.ws_manager = WebSocketConnectionManager()
        self.dispatcher_task: asyncio.Task = None
        logger.info("EventingManager initialized with Bus, Registry, Dispatcher, and WebSocket Manager.")

    def register_subscribers(self):
        """
        Tự động tìm và import các module subscriber để kích hoạt các decorator.
        Chỉ cần import file subscribers.py là đủ.
        """
        from ..event_bus import subscribers
        logger.info("Subscribers module imported, auto-registration complete.")

    def start(self):
        """Khởi động dispatcher trong một background task."""
        if self.dispatcher_task is None:
            self.dispatcher_task = asyncio.create_task(self.dispatcher.start())
            logger.info("Event dispatcher background task started.")

    def get_dependency(self, dep_type: Type) -> Any:
        """
        Cung cấp dependency dựa trên type hint.
        Đây là cốt lõi của cơ chế Dependency Injection.
        """
        # Chỉ inject các service/manager toàn cục.
        # Các Repository sẽ được Dispatcher xử lý riêng.
        if dep_type is WebSocketConnectionManager:
            return self.ws_manager
        if dep_type is ContextEngine:
            return self.context_engine
        if dep_type is StorageEngine:
            return self.storage
        
        # Trả về chính kiểu dữ liệu nếu nó là một lớp con của BaseRepository
        # để báo hiệu cho Dispatcher biết đây là một dependency cần UoW.
        try:
            if issubclass(dep_type, (SessionRepository.__base__)): # Kiểm tra lớp cha của Repo
                return dep_type
        except TypeError:
            pass # Bỏ qua nếu dep_type không phải là class
        return None