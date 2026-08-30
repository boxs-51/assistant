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
