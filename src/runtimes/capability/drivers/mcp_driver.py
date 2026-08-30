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
