import pytest

from src.application.policy.authorization import AuthorizationService
from src.domain.schemas.identity import Identity
from src.runtimes.capability.drivers.base import BaseCapabilityDriver, CapabilityDefinition
from src.runtimes.capability.runtime import CapabilityRuntime
from src.tool.registry import ToolRegistry
from src.domain.schemas.tool import GatewayToolDefinition


class EchoDriver(BaseCapabilityDriver):
    async def execute(self, arguments, context):
        return arguments["value"]


def make_identity(*scopes):
    return Identity(
        user_id="user-1",
        organization_id="org-1",
        auth_type="api_key",
        scopes=set(scopes),
    )


def test_authorization_requires_all_declared_scopes():
    driver = EchoDriver(CapabilityDefinition(
        name="github.search",
        description="Search GitHub",
        parameters={"type": "object"},
        require_auth=True,
        required_scopes=["github.read"],
    ))
    service = AuthorizationService()

    assert service.is_allowed(make_identity("github.read"), driver)
    assert not service.is_allowed(make_identity(), driver)
    assert not service.is_allowed(None, driver)


@pytest.mark.asyncio
async def test_capability_runtime_filters_and_executes_authorized_driver():
    runtime = CapabilityRuntime()
    driver = EchoDriver(CapabilityDefinition(
        name="github.search",
        description="Search GitHub",
        parameters={"type": "object"},
        require_auth=True,
        required_scopes=["github.read"],
    ))
    runtime.register_tool(driver)

    assert len(await runtime.get_available_tools(make_identity())) == 0
    assert len(await runtime.get_available_tools(make_identity("github.read"))) == 1
    assert await runtime.execute_tool("github.search", {"value": "ok"}, make_identity("github.read")) == "ok"
    with pytest.raises(PermissionError):
        await runtime.execute_tool("github.search", {"value": "no"}, make_identity())


def test_tool_registry_delegates_metadata_to_capability_registry():
    runtime = CapabilityRuntime()
    registry = ToolRegistry(runtime.registry)
    registry.register(GatewayToolDefinition(
        name="local.echo",
        description="Echo",
        parameters={"type": "object"},
        require_auth=False,
    ))

    assert runtime.registry.get_definition("local.echo") is not None
    assert registry.get("local.echo").name == "local.echo"
