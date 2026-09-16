import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.tool import GatewayToolDefinition
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.drivers.base import BaseCapabilityDriver
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.transport.gateway.api.v1.capability_router import register_tool_capability
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition 
from se.src.tool.registry import ToolRegistry
class ServerEchoDriver(BaseCapabilityDriver):
    async def execute(self, context, arguments):
        return arguments["value"]


class Container:
    pass


def _container():
    definition = CapabilityDefinition(
        id="echo",
        name="echo",
        description="Echo",
        input_schema={"type": "object"},
    )
    registry = CapabilityRegistry()
    registry.register_capability(ServerEchoDriver(definition))
    catalog = CapabilityCatalog()
    runtime = CapabilityRuntime(registry=registry, catalog=catalog)
    tool_registry = ToolRegistry()
    container = Container()
    container.capability_runtime = runtime
    container.tool_registry = tool_registry
    return container


def test_tool_endpoint_registers_server_implementation():
    async def scenario():
        container = _container()
        body = GatewayToolDefinition(
            name="echo",
            description="Echo",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        )
        identity = Identity(user_id="user-1", auth_type="jwt")
        response = await register_tool_capability(body, identity, container)
        assert response.kind == "TOOL"
        assert [x["implementation_id"] for x in response.implementations] == [
            "server:echo"
        ]
        assert response.implementations[0]["location"] == "SERVER"

    asyncio.run(scenario())


def test_tool_endpoint_does_not_claim_non_executable_definition():
    async def scenario():
        definition = CapabilityDefinition(
            id="remote-only",
            name="remote-only",
            description="Remote",
            input_schema={"type": "object"},
        )
        registry = CapabilityRegistry()
        catalog = CapabilityCatalog()
        runtime = CapabilityRuntime(registry=registry, catalog=catalog)
        container = SimpleNamespace(
            capability_runtime=runtime,
            tool_registry=ToolRegistry(),
        )
        body = GatewayToolDefinition(
            name="remote-only",
            description="Remote",
            parameters={"type": "object"},
        )
        identity = Identity(user_id="user-1", auth_type="jwt")

        try:
            await register_tool_capability(body, identity, container)
        except HTTPException as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError("Expected 422 for non-executable server tool")

    asyncio.run(scenario())