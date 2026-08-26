from typing import Optional

import structlog

from ..application.container import ApplicationContainer
from ..infrastructure.event_bus.manager import EventingManager

from .base import BaseRuntime, LifecycleState, RuntimeContext
from .lifecycle import HealthMonitor, LifecycleManager
from .registry import RuntimeRegistry

logger = structlog.get_logger(__name__)

class RuntimeKernel:
    def __init__(
        self,
        eventing_manager: EventingManager,
        container: ApplicationContainer,
    ):
        self.eventing_manager = eventing_manager
        self.event_bus = eventing_manager.bus
        self.container = container
        self.registry = RuntimeRegistry()
        self.lifecycle_manager = LifecycleManager(
            self.registry,
            self,
        )
        self.health_monitor = HealthMonitor(
            self.registry,
            self,
        )
        self.context: Optional[RuntimeContext] = None

    def register_runtime(self, runtime: BaseRuntime) -> None:
        self.registry.register(runtime)

    def get_service(self, service_name: str) -> Optional[BaseRuntime]:
        """Dành cho Infrastructure Services."""
        return self.registry.get_service(service_name)

    async def bootstrap(self) -> None:
        """Bootstrap toàn bộ runtime bằng một RuntimeContext dùng chung."""
        logger.info("=== BẮT ĐẦU BOOTSTRAP RUNTIME KERNEL ===")

        self.context = RuntimeContext(
            kernel=self,
            container=self.container,
            config=self.container.config,
            logger=logger,
            event_bus=self.container.event_bus,
            storage=self.container.storage,
            uow_factory=self.container.uow_factory,
            http_client=self.container.http_client,
            metrics=self.container.metrics,
            tracer=self.container.tracer,
            clock=self.container.clock,
        )

        self.eventing_manager.start()
        await self.lifecycle_manager.initialize_all(self.context)
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
        """Khôi phục Runtime nếu bị Crash."""
        runtime = self.registry.get(runtime_id)
        if not runtime or self.context is None:
            return

        logger.warning(
            "[Recovery Engine] Recreating Runtime",
            runtime_id=runtime_id,
        )

        try:
            await runtime.stop()
            await runtime.dispose()

            # Giả lập Re-initialize & Re-start
            runtime.initialize(self.context)
            await runtime.start()
            runtime.state = LifecycleState.RUNNING

            logger.info(
                "[Recovery Engine] Runtime recovered",
                runtime_id=runtime_id,
            )
            
        except Exception as ex:

            logger.critical(
                "[Recovery Engine] Runtime recovery failed",
                runtime_id=runtime_id,
                error=str(ex),
            )
            
            runtime.state = LifecycleState.FAILED