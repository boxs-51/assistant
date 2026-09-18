# src/runtime/runtimes/capability/drivers/python_driver.py
import inspect
from typing import Any, Callable, Mapping

from ..contracts.context import CapabilityExecutionContext
from .base import BaseCapabilityDriver, CapabilityDefinition

class PythonCapabilityDriver(BaseCapabilityDriver):
    """Driver for sync/async Python callables."""

    def __init__(self, definition: CapabilityDefinition, handler: Callable[..., Any]):
        super().__init__(definition)
        self._handler = handler

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        result = self._handler(**arguments)
        if inspect.isawaitable(result):
            return await result
        return result