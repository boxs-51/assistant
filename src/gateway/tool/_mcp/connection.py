import asyncio
from enum import Enum
from typing import Dict, Any, List, Optional


class ConnectionStatus(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAULTED = "FAULTED"

class McpConnection:
    """
    Quản lý thực thể kết nối độc lập của một MCP Server (Connection State Layer).
    Tích hợp bộ nhớ đệm (Cache) và theo dõi sức khỏe kết nối.
    """
    def __init__(self, server_name: str, command: str, args: List[str]):
        self.server_name = server_name
        self.command = command
        self.args = args
        
        self.session: Optional[ClientSession] = None
        self.status = ConnectionStatus.DISCONNECTED
        self.retry_count = 0
        self.last_error: Optional[str] = None
        
        # Cache Tool Layer (Mục 10)
        self.cached_tools: List[Dict[str, Any]] = []
        self.is_cache_valid = False

    def invalidate_cache(self):
        self.cached_tools = []
        self.is_cache_valid = False