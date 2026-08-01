

from typing import Any, Dict, Optional
import structlog

from ..infrastructure.event_bus.manager import EventingManager

from .base import BaseRuntime, LifecycleState
from .lifecycle import HealthMonitor, LifecycleManager
from .registry import RuntimeRegistry

logger = structlog.get_logger(__name__)

class RuntimeKernel:
    def __init__(self,eventing_manager: EventingManager):
        self.eventing_manager = eventing_manager
        self.event_bus = eventing_manager.bus
        self.registry = RuntimeRegistry()
        self.lifecycle_manager = LifecycleManager(self.registry, self)
        self.health_monitor = HealthMonitor(self.registry, self)

    def register_runtime(self, runtime: BaseRuntime) -> None:
        self.registry.register(runtime)

    def get_service(self, service_name: str) -> Optional[BaseRuntime]:
        """Dành cho Hạ Tầng (Infrastructure Services) - Section 9."""
        return self.registry.get_service(service_name)

    async def bootstrap(self, global_config: Dict[str, Any]) -> None:
        """Boot process của toàn hệ thống (Section 7)."""
        logger.info("=== BẮT ĐẦU BOOTSTRAP RUNTIME KERNEL ===")
        self.eventing_manager.start()
        await self.lifecycle_manager.initialize_all(global_config)
        await self.lifecycle_manager.start_all()
        self.health_monitor.start()
        logger.info("=== RUNTIME KERNEL ĐÃ SẴN SÀNG CHẠY ===")

    async def shutdown(self) -> None:
        logger.info("=== BẮT ĐẦU SHUTDOWN RUNTIME KERNEL ===")
        await self.health_monitor.stop()
        await self.lifecycle_manager.stop_all()
        await self.eventing_manager.shutdown()
        logger.info("=== HE THONG DA DISPOSED AN TOAN ===")

    async def recover_runtime(self, runtime_id: str) -> None:
        """Khôi phục Runtime nếu bị Crash (Section 16)."""
        runtime = self.registry.get(runtime_id)
        if not runtime:
            return

        logger.warning(f"[Recovery Engine] Đang tái tạo Runtime: {runtime_id}")
        try:
            await runtime.stop()
            await runtime.dispose()

            # Giả lập Re-initialize & Re-start
            runtime.initialize(runtime.context)
            await runtime.start()
            runtime.state = LifecycleState.RUNNING
            logger.info(f"[Recovery Engine] Tái tạo thành công Runtime '{runtime_id}' -> State: RUNNING")
        except Exception as ex:
            logger.critical(f"[Recovery Engine] Tái tạo Runtime '{runtime_id}' THẤT BẠI: {ex}")
            runtime.state = LifecycleState.FAILED