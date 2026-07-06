from typing import Dict, Any, List, Optional

from ..schemas import GatewayToolDefinition, ToolType

from .executor import ExecutorRegistry
from .registry import ToolRegistry


class GatewayToolManager:
    def __init__(self, registry: ToolRegistry, executor_registry: ExecutorRegistry):
        self.registry = registry
        self.executor_registry = executor_registry

    async def inject_secret_tools(self, user_metadata: Dict[str, Any]) -> List[GatewayToolDefinition]:
        """Lọc và nạp tool ngầm dựa trên cấu hình quyền hoặc metadata của user."""
        all_tools = self.registry.get_all()
        injected_tools = []
        
        for tool in all_tools:
            if tool.tool_type == ToolType.LOCAL or tool.tool_type == ToolType.NATIVE:
                injected_tools.append(tool)
            elif tool.tool_type == ToolType.MCP:
                # Chỉ nạp nếu thỏa mãn điều kiện Auth trong metadata
                if tool.source_server == "gdrive" and "google_access_token" in user_metadata:
                    injected_tools.append(tool)
                elif tool.source_server == "github" and "github_token" in user_metadata:
                    injected_tools.append(tool)
                    
        return injected_tools

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        """Thực thi điều phối không còn một chữ 'if/elif' rẽ nhánh loại tool."""
        # 1. Tìm định nghĩa định danh của tool
        definition = self.registry.get(tool_name)
        if not definition:
            return f"❌ Tool '{tool_name}' không tồn tại trong hệ thống Registry của Gateway."

        # 2. Lấy đúng Executor phụ trách dựa trên ToolType độc lập
        executor = self.executor_registry.get_executor(definition.tool_type)
        
        # 3. Tiến hành kích hoạt thực thi
        return await executor.execute(definition, arguments, user_metadata)