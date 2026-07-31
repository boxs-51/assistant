# src/runtime/runtimes/capability/drivers/base.py
from abc import ABC, abstractmethod
from typing import Any, Dict
from pydantic import BaseModel

class CapabilityDefinition(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema tuân thủ OpenAPI/OpenAI tool format
    require_auth: bool = False

class BaseCapabilityDriver(ABC):
    """Interface chuẩn cho mọi loại Tool/Capability trong hệ thống."""
    
    def __init__(self, definition: CapabilityDefinition):
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.name

    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """Hàm thực thi logic chính của Tool."""
        pass