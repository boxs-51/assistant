import asyncio
from typing import Dict, Any, Optional
import structlog

from ...kernel.base import BaseRuntime
from ...event_bus.bus import EventBus, EventDispatcher, EventPriority
from ...event_bus.registry import EventRegistry
from ...event_bus.ws_manager import WebSocketConnectionManager
from ...schemas.event import BaseEvent

logger = structlog.get_logger(__name__)


class EventRuntime(BaseRuntime):
    """
    Event Runtime đóng vai trò là Central Message Bus cho toàn bộ Runtime Subsystems.
    Nó bao bọc EventBus, EventDispatcher, và EventRegistry hiện tại.
    """

    def __init__(self):
        super().__init__("EventRuntime")
        self._bus: Optional[EventBus] = None
        self._registry: Optional[EventRegistry] = None
        self._dispatcher: Optional[EventDispatcher] = None
        self._ws_manager: Optional[WebSocketConnectionManager] = None
        self._dispatcher_task: Optional[asyncio.Task] = None

    @property
    def bus(self) -> EventBus:
        if not self._bus:
            raise RuntimeError("EventRuntime chưa được khởi tạo!")
        return self._bus

    @property
    def registry(self) -> EventRegistry:
        if not self._registry:
            raise RuntimeError("EventRuntime chưa được khởi tạo!")
        return self._registry

    @property
    def ws_manager(self) -> WebSocketConnectionManager:
        if not self._ws_manager:
            raise RuntimeError("EventRuntime chưa được khởi tạo!")
        return self._ws_manager

    async def initialize(self, context: Dict[str, Any]) -> None:
        """
        Khởi tạo các thành phần hạt nhân của Eventing System.
        context cần chứa: 'storage_engine' và 'uow_factory' (nếu có).
        """
        logger.info("Initializing Event Runtime...")

        storage_engine = context.get("storage_engine")
        uow_factory = context.get("uow_factory")

        # 1. Khởi tạo Event Priority Map
        priority_map = {
            "tool.execution.requested": EventPriority.HIGH,
            "tool.execution.completed": EventPriority.HIGH,
            "system.event.failed": EventPriority.HIGH,
            "chat.session.started": EventPriority.NORMAL,
            "capability.invoked": EventPriority.HIGH,
        }

        # 2. Khởi tạo Bus, Registry & WS Manager
        self._bus = EventBus(priority_map=priority_map)
        self._registry = EventRegistry()
        self._ws_manager = WebSocketConnectionManager()

        # 3. Khởi tạo Dispatcher
        cache_driver = storage_engine.drivers.get("cache") if storage_engine else None
        
        self._dispatcher = EventDispatcher(
            registry=self._registry,
            queue=self._bus.queue,
            dependency_container=self,  # Inject chính EventRuntime làm container
            cache_driver=cache_driver,
            uow_factory=uow_factory,
        )

        # 4. Đăng ký Subscribers tự động
        self._register_subscribers()
        logger.info("Event Runtime initialized successfully.")

    def _register_subscribers(self):
        """Import subscribers module để kích hoạt decorator đăng ký"""
        try:
            from ...event_bus import subscribers
            logger.info("Auto-registered Event Subscribers.")
        except ImportError as e:
            logger.warning(f"Could not auto-register subscribers: {e}")

    async def start(self) -> None:
        """Kích hoạt Dispatcher Loop chạy nền"""
        if self._dispatcher_task is None or self._dispatcher_task.done():
            self._dispatcher_task = asyncio.create_task(self._dispatcher.start())
            logger.info("Event Runtime Dispatcher loop started.")

    async def stop(self) -> None:
        """Hủy Task Dispatcher và dọn dẹp kết nối"""
        logger.info("Stopping Event Runtime...")
        if self._dispatcher_task and not self._dispatcher_task.done():
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
        logger.info("Event Runtime stopped cleanly.")

    async def publish(self, event: BaseEvent) -> asyncio.Future:
        """Helper API công khai cho phép các Runtime khác publish Event"""
        return self.bus.publish(event)

    def get_dependency(self, dep_type: type) -> Any:
        """
        Cơ chế Dependency Injection tương thích với EventDispatcher cũ.
        """
        if dep_type is WebSocketConnectionManager:
            return self._ws_manager
        if dep_type is EventRuntime:
            return self
        return None

    async def health_check(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "queue_size": self.bus.queue.qsize() if self._bus else 0,
            "active_ws_connections": len(self._ws_manager.active_connections) if self._ws_manager else 0
        }