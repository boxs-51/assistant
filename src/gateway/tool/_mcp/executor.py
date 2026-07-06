from typing import Dict, Any
from ..base.executor import BaseExecutor
from ...schemas import GatewayToolDefinition
from .mcp_manager import GatewayMcpManager
from ..credential import CredentialManager

class McpExecutor(BaseExecutor):
    def __init__(self, mcp_manager: GatewayMcpManager):
        self.mcp_manager = mcp_manager

    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        # Lấy session kết nối từ Pool của McpManager
        session = self.mcp_manager.get_raw_session(definition.source_server)
        if not session:
            return f"❌ [MCP EXECUTE LỖI] Server '{definition.source_server}' hiện đang mất kết nối hoặc ngoại tuyến."

        try:
            # Thu thập credential và thực thi lệnh qua Protocol chuẩn
            creds = CredentialManager.get_mcp_credentials(definition.source_server, user_metadata)
            extended_args = {**arguments, **creds}
            
            # Thực hiện lệnh gọi vật lý qua network ranh giới của MCP
            result = await session.call_tool(definition.name, arguments=extended_args)
            text_contents = [c.text for c in result.content if hasattr(c, 'text')]
            return "\n".join(text_contents)
        except Exception as e:
            return f"❌ [LỖI MCP PROTOCOL CALL]: {str(e)}"