from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Dict, List, Optional

from mcp import ClientSession


class ConnectionStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAULTED = "FAULTED"


class McpConnection:
    """State holder for one MCP server connection."""

    def __init__(self, server_name: str, command: str, args: List[str]):
        self.server_name = server_name
        self.command = command
        self.args = list(args)
        self.session: Optional[ClientSession] = None
        self.status = ConnectionStatus.DISCONNECTED
        self.retry_count = 0
        self.last_error: Optional[str] = None
        self.cached_tools: List[Dict[str, Any]] = []
        self.is_cache_valid = False
        self._lifecycle_task: Optional[asyncio.Task] = None

    def invalidate_cache(self) -> None:
        self.cached_tools = []
        self.is_cache_valid = False

    def bind_lifecycle_task(self, task: asyncio.Task) -> None:
        self._lifecycle_task = task

    @property
    def lifecycle_task(self) -> Optional[asyncio.Task]:
        return self._lifecycle_task
