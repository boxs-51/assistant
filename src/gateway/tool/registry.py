from typing import Dict, List, Optional
from ..schemas import GatewayToolDefinition

class ToolRegistry:
    """Quản lý việc lưu trữ toàn bộ định nghĩa Tool hiện có."""
    def __init__(self):
        self._tools: Dict[str, GatewayToolDefinition] = {}

    def register(self, definition: GatewayToolDefinition):
        self._tools[definition.name] = definition

    def get(self, name: str) -> Optional[GatewayToolDefinition]:
        return self._tools.get(name)

    def get_all(self) -> List[GatewayToolDefinition]:
        return list(self._tools.values())