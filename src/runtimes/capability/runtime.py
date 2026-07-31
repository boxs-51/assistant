# src/runtime/runtimes/capability/runtime.py
import structlog
from typing import Any, Dict
from ...kernel.base import BaseRuntime
from .registry import CapabilityRegistry
from .drivers.base import BaseCapabilityDriver
from ....schemas.identity import Identity

logger = structlog.get_logger(__name__)

class CapabilityRuntime(BaseRuntime):
    """Runtime quản lý toàn bộ vòng đời và thực thi các Capability/Tools."""

    def __init__(self):
        super().__init__(name="CapabilityRuntime")
        self.registry = CapabilityRegistry()

    async def initialize(self, context: Dict[str, Any]) -> None:
        """Khởi tạo các drivers hoặc đọc cấu hình plugin/tools nếu có."""
        self._is_initialized = True
        logger.info("Capability Runtime initialized.")

    async def start(self) -> None:
        self._is_running = True
        logger.info("Capability Runtime started.")

    async def stop(self) -> None:
        self._is_running = False
        logger.info("Capability Runtime stopped.")

    # --- Các public APIs để Gateway/Agent Runtime gọi sang ---

    def register_tool(self, driver: BaseCapabilityDriver):
        """API cho phép đăng ký tool mới vào Runtime."""
        self.registry.register_capability(driver)

    async def get_available_tools(self, identity: Identity):
        """Lấy danh sách các tool khả dụng cho LLM OpenAI Format."""
        return await self.registry.get_accessible_tools(identity)

    async def execute_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any], 
        identity: Identity,
        context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """API thực thi Tool có kiểm tra an toàn."""
        driver = self.registry.get_driver(tool_name)
        if not driver:
            raise ValueError(f"Capability/Tool '{tool_name}' not found.")

        # Lấy context thực thi (ví dụ UoW, StorageEngine nếu tool cần)
        exec_context = context or {}
        exec_context["identity"] = identity

        logger.info("Executing capability", tool_name=tool_name, arguments=arguments)
        try:
            result = await driver.execute(arguments, exec_context)
            return result
        except Exception as e:
            logger.error("Failed to execute capability", tool_name=tool_name, error=str(e))
            return f"Error executing tool '{tool_name}': {str(e)}"