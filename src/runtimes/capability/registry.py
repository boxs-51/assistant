# src/runtime/runtimes/capability/registry.py
import structlog
from typing import Dict, List, Optional
from .drivers.base import BaseCapabilityDriver, CapabilityDefinition
from ....schemas.identity import Identity

logger = structlog.get_logger(__name__)

class CapabilityRegistry:
    def __init__(self):
        self._drivers: Dict[str, BaseCapabilityDriver] = {}

    def register_capability(self, driver: BaseCapabilityDriver):
        if driver.name in self._drivers:
            logger.warning("Overwriting existing capability", name=driver.name)
        self._drivers[driver.name] = driver
        logger.info("Capability registered", name=driver.name)

    def get_driver(self, name: str) -> Optional[BaseCapabilityDriver]:
        return self._drivers.get(name)

    async def get_accessible_tools(self, identity: Identity) -> List[Dict[str, Any]]:
        """
        Lọc danh sách Tools dạng JSON Schema mà Identity hiện tại được phép dùng
        (Dùng để gửi vào request của LLM).
        """
        tools_schema = []
        for driver in self._drivers.values():
            # TODO: Có thể thêm logic RBAC / Permission check dựa theo identity ở đây
            tools_schema.append({
                "type": "function",
                "function": {
                    "name": driver.definition.name,
                    "description": driver.definition.description,
                    "parameters": driver.definition.parameters,
                }
            })
        return tools_schema