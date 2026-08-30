"""Compatibility import path; canonical implementation lives in infrastructure.mcp."""
from ...infrastructure.mcp.mcp_manager import GatewayMcpManager, McpToolDescriptor

__all__ = ["GatewayMcpManager", "McpToolDescriptor"]
