# src/kernel/lifecycle.py
import asyncio
from typing import Any, Optional

import structlog

from .registry import RuntimeRegistry, DependencyResolver
from .base import LifecycleState, HealthStatus, RuntimeContext

logger = structlog.get_logger(__name__)


class LifecycleManager:
    """Quản lý lifecycle của toàn bộ runtime bằng một RuntimeContext duy nhất."""

    def __init__(self, registry: RuntimeRegistry, kernel: Any):
        self.registry = registry
        self.kernel = kernel

    async def initialize_all(self, context: RuntimeContext) -> None:
        runtimes = self.registry.list_all()
        init_order = DependencyResolver.resolve_order(runtimes)

        logger.info(
            "Runtime initialization order",
            order=init_order,
        )

        for r_id in init_order:
            runtime = self.registry.get(r_id)
            if runtime is None:
                continue

            await runtime.initialize(context)
            if runtime.state != LifecycleState.INITIALIZED:
                runtime.state = LifecycleState.INITIALIZED

            logger.info(
                "Runtime initialized",
                runtime_id=r_id,
                state=runtime.state.value,
            )

    async def start_all(self) -> None:
        runtimes = self.registry.list_all()
        start_order = DependencyResolver.resolve_order(runtimes)

        for r_id in start_order:
            runtime = self.registry.get(r_id)
            if runtime and runtime.state == LifecycleState.INITIALIZED:
                await runtime.start()
                runtime.state = LifecycleState.RUNNING
                logger.info(
                    "Runtime started",
                    runtime_id=r_id,
                    state=runtime.state.value,
                )

    async def stop_all(self) -> None:
        runtimes = self.registry.list_all()
        stop_order = list(
            reversed(DependencyResolver.resolve_order(runtimes))
        )

        for r_id in stop_order:
            runtime = self.registry.get(r_id)
            if not runtime:
                continue

            if runtime.state not in (
                LifecycleState.RUNNING,
                LifecycleState.STARTED,
                LifecycleState.PAUSED,
            ):
                continue

            runtime.state = LifecycleState.STOPPING
            try:
                await runtime.stop()
            finally:
                runtime.state = LifecycleState.STOPPED
                await runtime.dispose()

            logger.info(
                "Runtime stopped",
                runtime_id=r_id,
                state=runtime.state.value,
            )


class HealthMonitor:
    """Định kỳ kiểm tra sức khỏe runtime và kích hoạt recovery."""

    def __init__(
        self,
        registry: RuntimeRegistry,
        kernel: Any,
        interval_sec: int = 5,
    ):
        self.registry = registry
        self.kernel = kernel
        self.interval_sec = interval_sec
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._check_loop())
        logger.info("Health Monitor started")

    async def stop(self) -> None:
        self._running = False

        task = self._monitor_task
        self._monitor_task = None

        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _check_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_sec)

                for runtime in self.registry.list_all():
                    if runtime.state != LifecycleState.RUNNING:
                        continue

                    try:
                        health = await runtime.check_health()
                        if health == HealthStatus.FAILED:
                            logger.error(
                                "Runtime health check failed",
                                runtime_id=runtime.manifest.id,
                            )
                            await self.kernel.recover_runtime(
                                runtime.manifest.id
                            )
                    except Exception as exc:
                        logger.exception(
                            "Runtime health check raised",
                            runtime_id=runtime.manifest.id,
                            error=str(exc),
                        )
                        runtime.state = LifecycleState.FAILED
                        await self.kernel.recover_runtime(
                            runtime.manifest.id
                        )
            except asyncio.CancelledError:
                break