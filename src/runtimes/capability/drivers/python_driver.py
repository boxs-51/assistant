# src/runtime/runtimes/capability/drivers/python_driver.py
from typing import Any, Callable, Dict, Coroutine
from .base import BaseCapabilityDriver, CapabilityDefinition

class PythonCapabilityDriver(BaseCapabilityDriver):
    """Driver cho phép chạy các hàm Python (Sync hoặc Async)."""

    def __init__(self, definition: CapabilityDefinition, handler: Callable[..., Any]):
        super().__init__(definition)
        self._handler = handler

    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        import asyncio
        if asyncio.iscoroutinefunction(self._handler):
            return await self._handler(**arguments)
        return self._handler(**arguments)