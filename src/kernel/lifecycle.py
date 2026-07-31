# src/kernel/lifecycle.py
import structlog
from typing import Dict, List
from .base import BaseRuntime, EventBus

logger = structlog.get_logger(__name__)

class RuntimeRegistry:
    def __init__(self):
        self._runtimes: Dict[str, BaseRuntime] = {}

    def register(self, runtime: BaseRuntime):
        self._runtimes[runtime.name] = runtime

    def get(self, name: str) -> BaseRuntime:
        if name not in self._runtimes:
            raise KeyError(f"Runtime '{name}' is not registered.")
        return self._runtimes[name]

class LifecycleManager:
    def __init__(self, registry: RuntimeRegistry, event_bus: EventBus):
        self.registry = registry
        self.event_bus = event_bus
        self._boot_order: List[str] = []

    def set_boot_order(self, order: List[str]):
        self._boot_order = order

    async def boot_sequence(self, global_context: Dict[str, Any]):
        # Initialize
        for name in self._boot_order:
            rt = self.registry.get(name)
            logger.info("Initializing runtime", runtime=name)
            await rt.initialize(global_context)

        # Start
        for name in self._boot_order:
            rt = self.registry.get(name)
            logger.info("Starting runtime", runtime=name)
            await rt.start()

    async def shutdown_sequence(self):
        for name in reversed(self._boot_order):
            rt = self.registry.get(name)
            logger.info("Stopping runtime", runtime=name)
            await rt.stop()