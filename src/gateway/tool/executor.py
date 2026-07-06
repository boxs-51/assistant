from abc import ABC, abstractmethod
from typing import Dict, Any, Callable

from ..schemas import GatewayToolDefinition, ToolType
from .credential import CredentialManager
from .base.executor import BaseExecutor

import asyncio


# ─── EXECUTOR CHO LOCAL TOOL ───
class LocalExecutor(BaseExecutor):
    def __init__(self):
        self._funcs: Dict[str, Callable] = {}

    def register_func(self, name: str, func: Callable):
        self._funcs[name] = func

    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        func = self._funcs.get(definition.name)
        if not func:
            return f"❌ Không tìm thấy hàm vật lý cho Local Tool: {definition.name}"
        try:
            if asyncio.iscoroutinefunction(func):
                return str(await func(**arguments))
            return str(func(**arguments))
        except Exception as e:
            return f"❌ Lỗi thực thi Local Tool: {str(e)}"

# ─── EXECUTOR CHO MCP TOOL ───
class McpExecutor(BaseExecutor):
    def __init__(self, mcp_manager):
        self.mcp_manager = mcp_manager

    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        # Gọi sang CredentialManager để lấy token tương ứng
        creds = CredentialManager.get_mcp_credentials(definition.source_server, user_metadata)
        # Gộp credentials vào chung với arguments truyền đi
        extended_args = {**arguments, **creds}
        
        # Gọi sang MCP Manager (truyền tên gốc của tool trên MCP Server)
        return await self.mcp_manager.execute_tool(definition.source_server, definition.name, extended_args)

# ─── EXECUTOR CHO NATIVE TOOL ───
class NativeExecutor(BaseExecutor):
    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        # Trường hợp này trên lý thuyết không xảy ra vì Provider tự execute, 
        # nhưng thiết kế sẵn để bảo vệ hệ thống nếu có cấu hình sai từ LLM Response Parser.
        return f"⚠️ Tool '{definition.name}' là Native Tool, việc thực thi thuộc trách nhiệm của AI Provider."

# ─── BỘ ĐĂNG KÝ VÀ ĐIỀU PHỐI CHÍNH (EXECUTOR REGISTRY) ───
class ExecutorRegistry:
    def __init__(self, local_exec: LocalExecutor, mcp_exec: McpExecutor, native_exec: NativeExecutor):
        self._executors: Dict[ToolType, BaseExecutor] = {
            ToolType.LOCAL: local_exec,
            ToolType.MCP: mcp_exec,
            ToolType.NATIVE: native_exec
        }

    def get_executor(self, tool_type: ToolType) -> BaseExecutor:
        return self._executors.get(tool_type)