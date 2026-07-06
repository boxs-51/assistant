from abc import ABC, abstractmethod
from typing import Dict, Any, Callable

from gateway.schemas.tool import GatewayToolDefinition

class BaseExecutor(ABC):
    @abstractmethod
    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        pass