from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from .connection import ConnectionStatus, McpConnection
from .factory import McpTransportFactory


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """Transport-neutral descriptor returned by MCP discovery."""

    server_name: str
    name: str
    description: str
    input_schema: Dict[str, Any]


class GatewayMcpManager:
    """Own MCP transport/session lifecycle and remote capability discovery."""

    def __init__(self, max_retries: int = 5, backoff_factor: float = 2.0):
        self._connections: Dict[str, McpConnection] = {}
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._health_check_task: Optional[asyncio.Task] = None
        self._stopping = False

    async def register_and_connect(
        self,
        server_name: str,
        command: str,
        args: Optional[List[str]] = None,
    ) -> None:
        args = list(args or [])
        existing = self._connections.get(server_name)
        if existing and existing.lifecycle_task and not existing.lifecycle_task.done():
            return

        conn = McpConnection(server_name, command, args)
        self._connections[server_name] = conn
        task = asyncio.create_task(self._lifecycle_manager(conn))
        conn.bind_lifecycle_task(task)

        # Preserve the old eager-connect contract without waiting forever.
        for _ in range(10):
            if conn.status == ConnectionStatus.CONNECTED:
                return
            await asyncio.sleep(0.5)

    async def start_health_checker(self) -> None:
        if self._health_check_task and not self._health_check_task.done():
            return
        self._stopping = False
        self._health_check_task = asyncio.create_task(self._health_check_loop())

    async def stop(self) -> None:
        self._stopping = True
        task = self._health_check_task
        self._health_check_task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        lifecycle_tasks = [
            c.lifecycle_task
            for c in self._connections.values()
            if c.lifecycle_task and not c.lifecycle_task.done()
        ]
        for task in lifecycle_tasks:
            task.cancel()
        for task in lifecycle_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        for conn in self._connections.values():
            conn.status = ConnectionStatus.DISCONNECTED
            conn.session = None
            conn.invalidate_cache()

    async def _health_check_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(30)
            for conn in list(self._connections.values()):
                if conn.status != ConnectionStatus.CONNECTED or conn.session is None:
                    continue
                try:
                    await conn.session.list_tools()
                except Exception as exc:
                    conn.status = ConnectionStatus.FAULTED
                    conn.last_error = str(exc)
                    conn.invalidate_cache()

    async def _lifecycle_manager(self, conn: McpConnection) -> None:
        params = McpTransportFactory.create_stdio_params(conn.command, conn.args)

        while not self._stopping:
            if conn.status not in {
                ConnectionStatus.DISCONNECTED,
                ConnectionStatus.FAULTED,
            }:
                await asyncio.sleep(0.5)
                continue

            conn.status = ConnectionStatus.CONNECTING
            try:
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        conn.session = session
                        conn.status = ConnectionStatus.CONNECTED
                        conn.retry_count = 0
                        conn.last_error = None
                        await self._refresh_tool_cache(conn)

                        while (
                            not self._stopping
                            and conn.status == ConnectionStatus.CONNECTED
                        ):
                            await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                conn.session = None
                conn.status = ConnectionStatus.DISCONNECTED
                conn.last_error = str(exc)
                conn.retry_count += 1
                delay = min(self.backoff_factor ** conn.retry_count, 60)
                await asyncio.sleep(delay)

    async def _refresh_tool_cache(self, conn: McpConnection) -> None:
        if conn.session is None:
            return
        result = await conn.session.list_tools()
        conn.cached_tools = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema or {},
            }
            for tool in result.tools
        ]
        conn.is_cache_valid = True

    async def get_tools_from_cache(self, server_name: str) -> List[McpToolDescriptor]:
        conn = self._connections.get(server_name)
        if not conn or conn.status != ConnectionStatus.CONNECTED:
            return []
        if not conn.is_cache_valid:
            await self._refresh_tool_cache(conn)
        return [
            McpToolDescriptor(
                server_name=server_name,
                name=item["name"],
                description=item.get("description", ""),
                input_schema=item.get("parameters", {}),
            )
            for item in conn.cached_tools
        ]

    async def get_all_active_servers(self) -> List[str]:
        return [
            name
            for name, conn in self._connections.items()
            if conn.status == ConnectionStatus.CONNECTED
        ]

    def get_raw_session(self, server_name: str) -> Optional[ClientSession]:
        conn = self._connections.get(server_name)
        if conn and conn.status == ConnectionStatus.CONNECTED:
            return conn.session
        return None
