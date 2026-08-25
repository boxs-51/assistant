# src/kernel/lifecycle.py
import logging

#from ..kernel.kernel import RuntimeKernel

from .registry import RuntimeRegistry, DependencyResolver
from .base import LifecycleState, HealthStatus, RuntimeContext
import asyncio
import structlog
from typing import Any, Dict, Optional

logger = structlog.get_logger(__name__)
class LifecycleManager:
    """Quản lý chuyển đổi trạng thái Vòng đời của toàn bộ hệ thống."""

    def __init__(self, registry: RuntimeRegistry, kernel: Any):
        self.registry = registry
        self.kernel = kernel

    async def initialize_all(self, global_config: Dict[str, Any]) -> None:
        runtimes = self.registry.list_all()
        init_order = DependencyResolver.resolve_order(runtimes)

        logger.info(f"Thứ tự khởi tạo Runtime: {' -> '.join(init_order)}")

        for r_id in init_order:
            runtime = self.registry.get(r_id)
            if not runtime:
                continue

            ctx = RuntimeContext(
                kernel=self.kernel,
                config=global_config,
                logger=structlog.get_logger(f"Runtime[{r_id}]"),
                event_bus=self.kernel.event_bus,
                container=global_config.get("container"),
                storage=global_config.get("storage_engine"),
                metrics=global_config.get("metrics"),
                clock=global_config.get("clock"),
            )
            await runtime.initialize(ctx)
            logger.info(f"Runtime '{r_id}' -> State: {runtime.state.value}")

    async def start_all(self) -> None:
        runtimes = self.registry.list_all()
        start_order = DependencyResolver.resolve_order(runtimes)

        for r_id in start_order:
            runtime = self.registry.get(r_id)
            if runtime and runtime.state == LifecycleState.INITIALIZED:
                await runtime.start()
                runtime.state = LifecycleState.RUNNING
                logger.info(f"Runtime '{r_id}' -> State: {runtime.state.value}")

    async def stop_all(self) -> None:
        runtimes = self.registry.list_all()
        # Dừng theo thứ tự ngược lại (Reverse Order)
        stop_order = list(reversed(DependencyResolver.resolve_order(runtimes)))

        for r_id in stop_order:
            runtime = self.registry.get(r_id)
            if runtime and runtime.state in [LifecycleState.RUNNING, LifecycleState.STARTED, LifecycleState.PAUSED]:
                runtime.state = LifecycleState.STOPPING
                await runtime.stop()
                runtime.state = LifecycleState.STOPPED
                await runtime.dispose()
                logger.info(f"Runtime '{r_id}' -> State: {runtime.state.value}")

class HealthMonitor:
    """Định kỳ Heartbeat kiểm tra sức khỏe và Recovery nếu Runtime bị Crash."""

    def __init__(self, registry: RuntimeRegistry, kernel: Any, interval_sec: int = 5):
        self.registry = registry
        self.kernel = kernel
        self.interval_sec = interval_sec
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._monitor_task = asyncio.create_task(self._check_loop())
        logger.info("Health Monitor đã kích hoạt.")

    async def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()

    async def _check_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_sec)
                for runtime in self.registry.list_all():
                    if runtime.state == LifecycleState.RUNNING:
                        try:
                            health = await runtime.check_health()
                            if health == HealthStatus.FAILED:
                                logger.error(f"⚠️ Runtime '{runtime.manifest.id}' FAILED! Kích hoạt Recovery...")
                                await self.kernel.recover_runtime(runtime.manifest.id)
                        except Exception as e:
                            logger.error(f"Lỗi khi kiểm tra Health Check của '{runtime.manifest.id}': {e}")
                            runtime.state = LifecycleState.FAILED
                            await self.kernel.recover_runtime(runtime.manifest.id)
            except asyncio.CancelledError:
                break