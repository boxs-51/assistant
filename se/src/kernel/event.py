import asyncio
from collections import defaultdict
from typing import Any, Dict, List, Callable
import structlog

logger = structlog.get_logger(__name__)


class InternalKernelBus:
    """Event Bus nội bộ của Kernel giúp các Runtime giao tiếp bất đồng bộ."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callable) -> None:
        self._subscribers[event_type].append(callback)

    async def publish(self, event_type: str, payload: Dict[str, Any]) -> None:
        logger.debug(f"[EventBus] Publish '{event_type}': {payload}")
        if event_type not in self._subscribers:
            return

        tasks = []
        for callback in self._subscribers[event_type]:
            if asyncio.iscoroutinefunction(callback):
                tasks.append(callback(payload))
            else:
                try:
                    callback(payload)
                except Exception as ex:
                    logger.error(f"[EventBus] Error in sync subscriber for '{event_type}': {ex}")

        if tasks:
            # Chờ tất cả async subscriber hoàn thành an toàn
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"[EventBus] Error in async subscriber for '{event_type}': {res}")