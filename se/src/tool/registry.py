import structlog
from typing import Dict, Optional, List

from ..domain.schemas.tool import GatewayToolDefinition

logger = structlog.get_logger(__name__)

class ToolRegistry:
    """
    Quản lý tập trung các "bản thiết kế" (definitions) của tất cả các tool trong hệ thống.
    Registry này không chứa logic thực thi, chỉ chứa metadata về tool.
    """
    def __init__(self, capability_registry=None):
        self._tools: Dict[str, GatewayToolDefinition] = {}
        self._capability_registry = capability_registry
        logger.info("ToolRegistry initialized.")

    def register(self, definition: GatewayToolDefinition):
        """Đăng ký một định nghĩa tool mới hoặc cập nhật định nghĩa đã có."""
        self._tools[definition.name] = definition
        if self._capability_registry is not None:
            from ..runtimes.capability.drivers.base import CapabilityDefinition

            self._capability_registry.register_definition(CapabilityDefinition(
                name=definition.name,
                description=definition.description,
                parameters=definition.parameters or {},
                require_auth=definition.require_auth,
                required_scopes=definition.required_scopes,
            ))
        logger.info(
            "Tool definition registered/updated successfully",
            tool_name=definition.name,
            tool_type=definition.tool_type.value if definition.tool_type else None,
        )

    def get(self, name: str) -> Optional[GatewayToolDefinition]:
        """Lấy định nghĩa của một tool dựa trên tên."""
        return self._tools.get(name)

    def get_all(self) -> List[GatewayToolDefinition]:
        """Lấy danh sách tất cả các định nghĩa tool đã được đăng ký."""
        return list(self._tools.values())

    def register_definition(self, definition: GatewayToolDefinition):
        self.register(definition)