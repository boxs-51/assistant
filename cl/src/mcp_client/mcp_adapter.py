import os
import shutil
import asyncio
from typing import Dict, Any, List
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClientAdapter:
    """Quản lý kết nối Stdio và ánh xạ Tool từ 1 MCP Server"""
    def __init__(self, server_name: str, server_config: dict):
        self.server_name = server_name
        self.config = server_config
        self.session: ClientSession = None
        self._exit_stack = AsyncExitStack()

    async def connect(self):
        env = os.environ.copy()
        if "env" in self.config:
            env.update(self.config["env"])

        cmd = self.config["command"]
        
        # SỬA LỖI [WinError 2]: Resolve đường dẫn tuyệt đối cho các lệnh npx/uvx/node trên Windows
        resolved_cmd = shutil.which(cmd)
        if resolved_cmd:
            cmd = resolved_cmd
        elif os.name == 'nt' and not cmd.endswith(('.cmd', '.exe', '.bat')):
            # Fallback thủ công nếu shutil.which không tìm ra
            for ext in ['.cmd', '.exe', '.bat']:
                if shutil.which(cmd + ext):
                    cmd = shutil.which(cmd + ext)
                    break

        server_params = StdioServerParameters(
            command=cmd,
            args=self.config.get("args", []),
            env=env
        )

        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        self.session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self.session.initialize()

    async def get_mapped_tools(self) -> Dict[str, Dict[str, Any]]:
        mcp_tools = await self.session.list_tools()
        mapped_tools = {}

        for tool in mcp_tools.tools:
            namespaced_name = f"mcp__{self.server_name}__{tool.name}"
            mapped_tools[namespaced_name] = {
                "metadata": {
                    "name": namespaced_name,
                    "description": f"[{self.server_name.upper()} MCP] {tool.description}",
                    "base_risk": self.config.get("base_risk", "MEDIUM"),
                    "parameters": tool.inputSchema
                },
                "func": self._create_execution_handler(tool.name),
                "is_mcp": True
            }
        return mapped_tools

    def _create_execution_handler(self, original_tool_name: str):
        def handler(**kwargs) -> str:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            result = loop.run_until_complete(
                self.session.call_tool(original_tool_name, arguments=kwargs)
            )
            output_texts = [c.text for c in result.content if c.type == "text"]
            return "\n".join(output_texts)
        return handler


class MCPManager:
    """Quản lý nhiều MCP Server Adapters cùng lúc"""
    def __init__(self):
        self.adapters: List[MCPClientAdapter] = []

    def load_mcp_servers(self, mcp_servers_config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        mcp_tools = {}
        for s_name, s_config in mcp_servers_config.items():
            if not s_config.get("enabled", True):
                print(f"⏸️ [MCP Disabled]: {s_name}")
                continue

            try:
                adapter = MCPClientAdapter(s_name, s_config)
                
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                loop.run_until_complete(adapter.connect())
                server_tools = loop.run_until_complete(adapter.get_mapped_tools())

                mcp_tools.update(server_tools)
                self.adapters.append(adapter)
                print(f"🔌 [MCP Connected]: {s_name} ({len(server_tools)} tools)")

            except Exception as e:
                print(f"❌ Lỗi kết nối MCP Server [{s_name}]: {e}")

        return mcp_tools