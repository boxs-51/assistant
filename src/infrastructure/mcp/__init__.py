"""MCP infrastructure: transport, connection lifecycle, health and discovery."""

from .connection import ConnectionStatus, McpConnection
from .factory import McpTransportFactory
from .mcp_manager import GatewayMcpManager, McpToolDescriptor

__all__ = [
    "ConnectionStatus",
    "McpConnection",
    "McpTransportFactory",
    "GatewayMcpManager",
    "McpToolDescriptor",
]
