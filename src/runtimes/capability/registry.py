# src/runtime/runtimes/capability/registry.py
import structlog
from typing import Dict, List, Optional, Any
from .drivers.base import BaseCapabilityDriver, CapabilityDefinition
from ...domain.schemas.identity import Identity

logger = structlog.get_logger(__name__)

class CapabilityRegistry:
    def __init__(self):
        self._drivers: Dict[str, BaseCapabilityDriver] = {}
        self._definitions: Dict[str, CapabilityDefinition] = {}

    def register_capability(self, driver: BaseCapabilityDriver):
        if driver.name in self._drivers:
            logger.warning("Overwriting existing capability", name=driver.name)
        self._drivers[driver.name] = driver
        self._definitions[driver.name] = driver.definition
        logger.info("Capability registered", name=driver.name)

    def register_definition(self, definition: CapabilityDefinition):
        self._definitions[definition.name] = definition
        logger.info("Capability definition registered", name=definition.name)

    def get_driver(self, name: str) -> Optional[BaseCapabilityDriver]:
        return self._drivers.get(name)

    def get_all_drivers(self) -> List[BaseCapabilityDriver]:
        return list(self._drivers.values())

    def get_definition(self, name: str) -> Optional[CapabilityDefinition]:
        return self._definitions.get(name)

    async def get_accessible_tools(self, identity: Identity) -> List[Dict[str, Any]]:
        """
        Lọc danh sách Tools dạng JSON Schema mà Identity hiện tại được phép dùng
        (Dùng để gửi vào request của LLM).
        """
        tools_schema = []
        for definition in self._definitions.values():
            tools_schema.append({
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.parameters,
                }
            })
        return tools_schema