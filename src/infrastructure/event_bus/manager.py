import structlog
import asyncio
from typing import Type, Any

from ..storage.core.manager import StorageEngine
from ..storage.repositories.sessions import SessionRepository
from ..storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .bus import EventBus, EventDispatcher, EventPriority
from .registry import EventRegistry
from .ws_manager import WebSocketConnectionManager

logger = structlog.get_logger(__name__)

class EventingManager:
    def __init__(self, storage_engine: StorageEngine):
        self.storage = storage_engine

        db_driver = self.storage.drivers.get("sqlite")
        # uow_factory cần truyền self.bus sau khi bus khởi tạo
        self.uow_factory = lambda: SqlAlchemyUnitOfWork(db_driver, self.bus)
        
        priority_map = {
            "tool.execution.requested": EventPriority.HIGH,
            "tool.execution.completed": EventPriority.HIGH,
            "system.event.failed": EventPriority.HIGH,
            "user.created": EventPriority.NORMAL,
            "chat.session.started": EventPriority.NORMAL,
        }
        
        # 1. Khởi tạo Registry trước
        self.registry = EventRegistry()
        
        # 2. Khởi tạo Bus truyền kèm Registry
        self.bus = EventBus(registry=self.registry, priority_map=priority_map)
        
        # 3. Khởi tạo Dispatcher
        self.dispatcher = EventDispatcher(
            registry=self.registry,
            queue=self.bus.queue,
            dependency_container=self,
            cache_driver=self.storage.drivers.get("cache"),
            uow_factory=self.uow_factory
        )
        self.ws_manager = WebSocketConnectionManager()
        self.dispatcher_task: asyncio.Task = None
        logger.info("EventingManager initialized with Bus, Shared Registry, Dispatcher, and WebSocket Manager.")

    def register_subscribers(self):
        from . import subscribers
        logger.info("Subscribers module imported, auto-registration complete.")

    def start(self):
        if self.dispatcher_task is None:
            self.dispatcher_task = asyncio.create_task(self.dispatcher.start())
            logger.info("Event dispatcher background task started.")

    async def shutdown(self):
        """Gracefully shutdown the eventing manager."""
        logger.info("Shutting down EventingManager...")
        if self.dispatcher_task and not self.dispatcher_task.done():
            self.dispatcher_task.cancel()
            try:
                await self.dispatcher_task
            except asyncio.CancelledError:
                logger.info("Event dispatcher task cancelled.")
        await self.ws_manager.shutdown()
        logger.info("EventingManager has been shut down.")

    def get_dependency(self, dep_type: Type) -> Any:
        if dep_type is WebSocketConnectionManager:
            return self.ws_manager
        if dep_type is StorageEngine:
            return self.storage
        
        try:
            # Kiểm tra lớp cha của Repo
            if hasattr(SessionRepository, '__base__') and issubclass(dep_type, SessionRepository.__base__):
                return dep_type
        except TypeError:
            pass
        return None