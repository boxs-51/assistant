"""Compatibility import path; canonical implementation lives in infrastructure.mcp."""
from ...infrastructure.mcp.connection import ConnectionStatus, McpConnection

__all__ = ["ConnectionStatus", "McpConnection"]
