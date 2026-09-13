#!/usr/bin/env bash
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

[[ -f src/runtimes/capability/contracts/definition.py ]] || {
  echo "Capability Contract v1 is required before Phase MCP." >&2
  exit 1
}
[[ -f src/runtimes/capability/contracts/context.py ]] || {
  echo "Capability Contract v1 is required before Phase MCP." >&2
  exit 1
}

mkdir -p src/infrastructure/mcp src/runtimes/capability/drivers src/tool/_mcp docs/phase3

cat > src/infrastructure/mcp/__init__.py <<'PY'
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
PY

cat > src/infrastructure/mcp/connection.py <<'PY'
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
PY

cat > src/infrastructure/mcp/factory.py <<'PY'
from typing import List

from mcp import StdioServerParameters


class McpTransportFactory:
    """Creates MCP transport configuration without owning Gateway logic."""

    @staticmethod
    def create_stdio_params(command: str, args: List[str]) -> StdioServerParameters:
        import os

        return StdioServerParameters(
            command=command,
            args=list(args),
            env=os.environ.copy(),
        )
PY

cat > src/infrastructure/mcp/mcp_manager.py <<'PY'
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
PY

cat > src/runtimes/capability/drivers/mcp_driver.py <<'PY'
from __future__ import annotations

from typing import Any, Dict, Mapping, Protocol

from ....domain.schemas.identity import Identity
from ....infrastructure.mcp.mcp_manager import GatewayMcpManager
from ..contracts.context import CapabilityExecutionContext
from ..contracts.definition import CapabilityDefinition
from .base import BaseCapabilityDriver


class McpCredentialResolver(Protocol):
    """Explicit security boundary for MCP credentials."""

    def resolve(
        self,
        server_name: str,
        identity: Identity | None,
        metadata: Mapping[str, Any],
    ) -> Dict[str, Any]: ...


class NullMcpCredentialResolver:
    """Safe default: do not implicitly inject credentials."""

    def resolve(
        self,
        server_name: str,
        identity: Identity | None,
        metadata: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return {}


class McpCapabilityDriver(BaseCapabilityDriver):
    """Execution adapter from Gateway Capability to MCP protocol."""

    def __init__(
        self,
        definition: CapabilityDefinition,
        mcp_manager: GatewayMcpManager,
        credential_resolver: McpCredentialResolver | None = None,
    ) -> None:
        super().__init__(definition)
        self._mcp_manager = mcp_manager
        self._credential_resolver = credential_resolver or NullMcpCredentialResolver()

    @property
    def server_name(self) -> str:
        server_name = self.definition.metadata.get("mcp_server")
        if not isinstance(server_name, str) or not server_name:
            raise ValueError(
                f"Capability '{self.name}' is missing metadata.mcp_server"
            )
        return server_name

    async def check_health(self) -> bool:
        return self._mcp_manager.get_raw_session(self.server_name) is not None

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        session = self._mcp_manager.get_raw_session(self.server_name)
        if session is None:
            raise RuntimeError(
                f"MCP server '{self.server_name}' is unavailable."
            )

        credentials = self._credential_resolver.resolve(
            self.server_name,
            context.identity,
            context.metadata,
        )
        extended_arguments = {**dict(arguments), **credentials}
        remote_name = self.definition.metadata.get(
            "mcp_tool_name",
            self.definition.name,
        )

        result = await session.call_tool(
            remote_name,
            arguments=extended_arguments,
        )

        text_parts = [
            item.text
            for item in getattr(result, "content", [])
            if hasattr(item, "text")
        ]
        return "\n".join(text_parts)
PY

cat > src/tool/_mcp/connection.py <<'PY'
"""Compatibility import path; canonical implementation lives in infrastructure.mcp."""
from ...infrastructure.mcp.connection import ConnectionStatus, McpConnection

__all__ = ["ConnectionStatus", "McpConnection"]
PY

cat > src/tool/_mcp/factory.py <<'PY'
"""Compatibility import path; canonical implementation lives in infrastructure.mcp."""
from ...infrastructure.mcp.factory import McpTransportFactory

__all__ = ["McpTransportFactory"]
PY

cat > src/tool/_mcp/mcp_manager.py <<'PY'
"""Compatibility import path; canonical implementation lives in infrastructure.mcp."""
from ...infrastructure.mcp.mcp_manager import GatewayMcpManager, McpToolDescriptor

__all__ = ["GatewayMcpManager", "McpToolDescriptor"]
PY

cat > src/tool/_mcp/executor.py <<'PY'
"""Legacy adapter around the canonical MCP Capability Driver.

No MCP transport or protocol execution logic is kept in src/tool.
"""

from typing import Any, Dict

from ..base.executor import BaseExecutor
from ...domain.schemas import GatewayToolDefinition
from ...domain.schemas.identity import Identity
from ...infrastructure.mcp.mcp_manager import GatewayMcpManager
from ...runtimes.capability.contracts.context import CapabilityExecutionContext
from ...runtimes.capability.contracts.definition import CapabilityDefinition
from ...runtimes.capability.drivers.mcp_driver import McpCapabilityDriver


class McpExecutor(BaseExecutor):
    def __init__(self, mcp_manager: GatewayMcpManager):
        self.mcp_manager = mcp_manager

    async def execute(
        self,
        definition: GatewayToolDefinition,
        arguments: Dict[str, Any],
        user_metadata: Dict[str, Any],
    ) -> str:
        server_name = definition.source_server
        if not server_name:
            raise ValueError(
                f"MCP Tool '{definition.name}' is missing source_server."
            )

        capability = CapabilityDefinition(
            id=f"{server_name}:{definition.name}",
            name=f"{server_name}:{definition.name}",
            description=definition.description,
            input_schema=definition.parameters or {},
            source="MCP",
            execution_kind="MCP",
            require_auth=definition.require_auth,
            required_scopes=definition.required_scopes,
            metadata={
                "mcp_server": server_name,
                "mcp_tool_name": definition.name,
            },
        )
        identity = Identity.model_validate(user_metadata)
        context = CapabilityExecutionContext.create(
            identity=identity,
            session_id=identity.session_id,
            metadata={"legacy_user_metadata": dict(user_metadata)},
        )
        driver = McpCapabilityDriver(capability, self.mcp_manager)
        result = await driver.execute(context, arguments)
        return str(result)
PY

./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path

# Wire MCP infrastructure into ApplicationContainer if not already present.
p = Path("src/application/container.py")
s = p.read_text(encoding="utf-8")
if "mcp_manager:" not in s:
    s = s.replace(
        "    eventing_manager: Any\n",
        "    eventing_manager: Any\n    mcp_manager: Optional[Any] = None\n",
        1,
    )
p.write_text(s, encoding="utf-8")

# Wire manager creation/start/stop in main bootstrap.
p = Path("src/main.py")
s = p.read_text(encoding="utf-8")
if "from .infrastructure.mcp import GatewayMcpManager" not in s:
    s = s.replace(
        "from .infrastructure.event_bus.bus import EventBus\n",
        "from .infrastructure.event_bus.bus import EventBus\nfrom .infrastructure.mcp import GatewayMcpManager\n",
        1,
    )
if "mcp_manager=GatewayMcpManager()" not in s:
    s = s.replace(
        "        eventing_manager=eventing_manager,\n",
        "        eventing_manager=eventing_manager,\n        mcp_manager=GatewayMcpManager(),\n",
        1,
    )
if "await container.mcp_manager.start_health_checker()" not in s:
    s = s.replace(
        "    eventing_manager.set_dependency_container(container)\n",
        "    eventing_manager.set_dependency_container(container)\n    await container.mcp_manager.start_health_checker()\n",
        1,
    )
if "await container.mcp_manager.stop()" not in s:
    s = s.replace(
        "        if container.runtime_kernel:\n            await container.runtime_kernel.shutdown()\n\n        await http_client.aclose()",
        "        if container.runtime_kernel:\n            await container.runtime_kernel.shutdown()\n\n        if container.mcp_manager:\n            await container.mcp_manager.stop()\n\n        await http_client.aclose()",
        1,
    )
p.write_text(s, encoding="utf-8")

# Wire canonical MCP discovery into the v1 CapabilityRuntime.
p = Path("src/runtimes/capability/runtime.py")
s = p.read_text(encoding="utf-8")
if "from .drivers.mcp_driver import McpCapabilityDriver" not in s:
    s = s.replace(
        "from .contracts.result import CapabilityResult\n",
        "from .contracts.result import CapabilityResult\nfrom .drivers.mcp_driver import McpCapabilityDriver\n",
        1,
    )
if "self.mcp_manager = None" not in s:
    s = s.replace(
        "        self._subscribed = False\n",
        "        self._subscribed = False\n        self.mcp_manager = None\n",
        1,
    )
if "self.mcp_manager = getattr(context.container, \"mcp_manager\", None)" not in s:
    s = s.replace(
        "        self.event_bus = context.event_bus\n",
        "        self.event_bus = context.event_bus\n        self.mcp_manager = getattr(context.container, \"mcp_manager\", None)\n",
        1,
    )
if "async def discover_mcp_capabilities" not in s:
    marker = "    async def execute_capability(\n"
    method = '''    async def discover_mcp_capabilities(self, server_name: str) -> int:\n        """Discover remote MCP tools and register them as executable capabilities."""\n        if self.mcp_manager is None:\n            raise RuntimeError("MCP infrastructure is not available.")\n\n        descriptors = await self.mcp_manager.get_tools_from_cache(server_name)\n        for descriptor in descriptors:\n            definition = CapabilityDefinition(\n                id=f"{descriptor.server_name}:{descriptor.name}",\n                version="1.0",\n                name=f"{descriptor.server_name}:{descriptor.name}",\n                description=descriptor.description,\n                input_schema=descriptor.input_schema,\n                source="MCP",\n                execution_kind="MCP",\n                metadata={\n                    "mcp_server": descriptor.server_name,\n                    "mcp_tool_name": descriptor.name,\n                },\n            )\n            self.register_capability(\n                McpCapabilityDriver(definition, self.mcp_manager)\n            )\n        return len(descriptors)\n\n'''
    if marker in s:
        s = s.replace(marker, method + marker, 1)
    else:
        raise SystemExit("Could not locate execute_capability in CapabilityRuntime")

p.write_text(s, encoding="utf-8")
PY

cat > docs/phase3/PHASE3_MCP_CAPABILITY_DESIGN.md <<'MD'
# Phase 3 — MCP Capability Driver

## Architectural goal

Move MCP **execution semantics** out of `src/tool/_mcp` while keeping MCP
**transport, connection lifecycle, reconnect, health and discovery cache** in
infrastructure. `CapabilityRuntime` becomes the Gateway execution boundary.

```text
MCP Server
    |
    v
infrastructure.mcp
    | transport / session / reconnect / health / discovery
    v
McpToolDescriptor
    |
    v
CapabilityRuntime.discover_mcp_capabilities()
    |
    +--> CapabilityDefinition
    +--> McpCapabilityDriver
    |
    v
CapabilityRegistry
    |
    v
CapabilityRuntime.execute_capability()
```

## Security boundary

The driver never reads tokens from `Identity.scopes`. Credentials are resolved
through an explicit `McpCredentialResolver` dependency. The default resolver
injects nothing. A later security phase can provide the real resolver without
coupling MCP infrastructure to authentication storage.

## Compatibility boundary

`src/tool/_mcp/*` is reduced to import/adaptor shims. It remains only so older
imports can survive while the rest of `src/tool` is migrated. No MCP network
or connection logic remains there.

## Invariants

1. MCP infrastructure does not import `ToolRegistry`.
2. MCP infrastructure does not perform Gateway authorization.
3. Discovery creates `McpToolDescriptor`; CapabilityRuntime creates the domain
   `CapabilityDefinition` and driver.
4. Only CapabilityRuntime invokes MCP capabilities in the new path.
5. A disconnected MCP server makes the driver unhealthy/unavailable without
   deleting the capability definition.
6. Remote MCP name and Gateway capability id are distinct and explicitly
   mapped (`metadata.mcp_tool_name`).
MD

./.venv/Scripts/python.exe -m compileall -q src/infrastructure/mcp src/runtimes/capability src/tool/_mcp
./.venv/Scripts/python.exe -m pytest -q tests/architecture/test_phase3.py

echo "Phase MCP Capability v1 applied successfully."
