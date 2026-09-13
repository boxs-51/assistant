# src/runtime/runtimes/capability/drivers/base.py
from abc import ABC, abstractmethod
from typing import Any, Mapping

from ..contracts.context import CapabilityExecutionContext
from ..contracts.definition import CapabilityDefinition

class BaseCapabilityDriver(ABC):
    """Execution boundary for one concrete capability implementation."""

    
    def __init__(self, definition: CapabilityDefinition):
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.capability_id

    async def initialize(self, context: Any) -> None:
        """Optional lifecycle hook for resource-backed drivers."""
        return None

    async def check_health(self) -> bool:
        return True

    async def dispose(self) -> None:
        return None

    @abstractmethod
    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        """Execute one invocation.

        The return value may remain a raw Python value for compatibility; the
        CapabilityRuntime normalizes it into ``CapabilityResult``.
        """
        pass