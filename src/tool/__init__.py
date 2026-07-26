import time
from typing import Dict, Any, List, Optional

from ..schemas import GatewayToolDefinition, ToolType
from ..schemas.identity import  Identity
from ..schemas.event import BaseEvent

from .executor import ExecutorRegistry
from .registry import ToolRegistry
from ..event_bus.bus import EventBus


class GatewayToolManager:
    def __init__(self, registry: ToolRegistry, executor_registry: ExecutorRegistry, event_bus: EventBus):
        self.registry = registry
        self.executor_registry = executor_registry
        self.event_bus = event_bus

    async def get_accessible_tools(self, identity: Identity) -> List[GatewayToolDefinition]:
        """Lọc và nạp tool ngầm dựa trên cấu hình quyền hoặc metadata của user."""
        all_tools = self.registry.get_all()
        accessible_tools = []
        
        for tool in all_tools:
            if tool.tool_type == ToolType.LOCAL or tool.tool_type == ToolType.NATIVE:
                accessible_tools.append(tool)
            elif tool.tool_type == ToolType.MCP:
                # Chỉ nạp nếu thỏa mãn điều kiện Auth trong metadata
                # Ví dụ: Kiểm tra scopes trong identity
                if tool.source_server == "gdrive" and "gdrive.read" in identity.scopes:
                    accessible_tools.append(tool)
                elif tool.source_server == "github" and "github.read" in identity.scopes:
                    accessible_tools.append(tool)
                    
        return accessible_tools

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any], identity: Identity) -> str:
        """Thực thi điều phối không còn một chữ 'if/elif' rẽ nhánh loại tool."""
        # 1. Tìm định nghĩa định danh của tool
        definition = self.registry.get(tool_name)
        if not definition:
            return f"❌ Tool '{tool_name}' không tồn tại trong hệ thống Registry của Gateway."

        # 2. Lấy đúng Executor phụ trách dựa trên ToolType
        executor = self.executor_registry.get_executor(definition.tool_type)
        if not executor:
            return f"❌ Không tìm thấy Executor cho loại tool '{definition.tool_type.value}'."
        
        # 3. Phát sự kiện bắt đầu thực thi
        start_time = time.monotonic()
        # await self.event_bus.publish(BaseEvent(
        #     event_name="tool.execution.started",
        #     payload={
        #         "tool_name": tool_name,
        #         "arguments": arguments,
        #         "session_id": identity.session_id,
        #         "user_id": identity.user_id,
        #     }
        # ))

        is_error = False
        result = ""
        try:
            # 4. Tiến hành kích hoạt thực thi
            user_metadata_dict = identity.model_dump()
            result = await executor.execute(definition, arguments, user_metadata_dict)
        except Exception as e:
            result = f"❌ Lỗi hệ thống khi thực thi tool '{tool_name}': {e}"
            is_error = True
        finally:
            # 5. Phát sự kiện kết thúc thực thi
            duration_ms = (time.monotonic() - start_time) * 1000
            # await self.event_bus.publish(BaseEvent(
            #     event_name="tool.execution.completed",
            #     payload={
            #         "tool_name": tool_name,
            #         "duration_ms": round(duration_ms, 2),
            #         "is_error": is_error,
            #         "session_id": identity.session_id,
            #         "user_id": identity.user_id,
            #     }
            # ))
        
        return result