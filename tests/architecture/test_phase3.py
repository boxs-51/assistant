import pytest

from src.application.policy.authorization import AuthorizationService
from src.domain.schemas.identity import Identity
from src.runtimes.capability.registry import CapabilityRegistry, CapabilityState
from src.runtimes.capability.contracts.context import CapabilityExecutionContext
from src.runtimes.capability.contracts.error import CapabilityError
from src.runtimes.capability.contracts.result import CapabilityResult
from src.runtimes.capability.drivers.base import BaseCapabilityDriver, CapabilityDefinition
from src.runtimes.capability.drivers.mcp_driver import McpCapabilityDriver
from src.runtimes.capability.runtime import CapabilityRuntime
from src.tool.registry import ToolRegistry
from src.domain.schemas.tool import GatewayToolDefinition
from src.kernel.base import HealthStatus

class EchoDriver(BaseCapabilityDriver):
    async def execute(self, context, arguments):
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


def test_capability_definition_is_transport_neutral():
    definition = CapabilityDefinition(
        id="github.search",
        version="1.0",
        name="github.search",
        description="Search GitHub",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        output_schema={"type": "object"},
        source="MCP",
        execution_kind="MCP",
    )
    assert definition.capability_id == "github.search"
    assert definition.parameters["type"] == "object"
    assert definition.input_schema["type"] == "object"


def test_capability_registry_does_not_treat_definition_only_as_executable():
    from src.runtimes.capability.registry import CapabilityRegistry

    registry = CapabilityRegistry()
    registry.register_definition(
        CapabilityDefinition(
            id="discovered.only",
            name="discovered.only",
            description="metadata only",
        )
    )
    assert registry.get_driver("discovered.only") is None
    assert registry.get("discovered.only").executable is False


def test_capability_execution_context_is_invocation_scoped():
    identity = make_identity("github.read")
    first = CapabilityExecutionContext.create(identity=identity)
    second = CapabilityExecutionContext.create(identity=identity)
    assert first.execution_id != second.execution_id
    assert first.invocation_id != second.invocation_id
    assert first.identity is identity


def test_capability_error_is_machine_readable():
    error = CapabilityError.from_exception(
        PermissionError("denied"),
        capability_id="github.search",
        invocation_id="capinv_1",
    )
    assert error.code == "CAPABILITY_UNAUTHORIZED"
    assert error.category == "AUTHORIZATION"
    assert error.safe_for_client is True


@pytest.mark.asyncio
async def test_execute_capability_returns_normalized_result_and_legacy_api_still_works():
    runtime = CapabilityRuntime()
    runtime.register_capability(
        EchoDriver(
            CapabilityDefinition(
                id="local.echo",
                name="local.echo",
                description="Echo",
                parameters={"type": "object"},
            )
        )
    )
    identity = make_identity()
    result = await runtime.execute_capability(
        "local.echo",
        {"value": "ok"},
        identity,
    )
    assert isinstance(result, CapabilityResult)
    assert result.success is True
    assert result.output == "ok"
    assert result.invocation_id
    assert await runtime.execute_tool(
        "local.echo",
        {"value": "legacy"},
        identity,
    ) == "legacy"

def test_capability_runtime_uses_injected_dependencies():
    registry = CapabilityRegistry()
    authorization = AuthorizationService()

    runtime = CapabilityRuntime(
        registry=registry,
        authorization=authorization,
    )

    assert runtime.registry is registry
    assert runtime.authorization is authorization


@pytest.mark.asyncio
async def test_mcp_capability_transitions_unavailable_and_recovers():
    class FakeMcpManager:
        def __init__(self):
            self.available = True

        def get_raw_session(self, _server_name):
            return object() if self.available else None

    manager = FakeMcpManager()
    registry = CapabilityRegistry()
    runtime = CapabilityRuntime(registry=registry)
    runtime.mcp_manager = manager

    definition = CapabilityDefinition(
        id="github:search",
        name="github:search",
        description="Search GitHub",
        source="MCP",
        execution_kind="MCP",
        metadata={
            "mcp_server": "github",
            "mcp_tool_name": "search",
        },
    )
    runtime.register_capability(
        McpCapabilityDriver(definition, manager)
    )

    assert registry.get("github:search").state is CapabilityState.ENABLED
    assert await runtime.check_health()
    assert registry.get("github:search").state is CapabilityState.ENABLED

    manager.available = False
    assert await runtime.check_health() == HealthStatus.DEGRADED
    assert (
        registry.get("github:search").state
        is CapabilityState.UNAVAILABLE
    )
    assert registry.get_driver("github:search") is None

    manager.available = True
    assert await runtime.check_health() == HealthStatus.HEALTHY
    assert registry.get("github:search").state is CapabilityState.ENABLED
    assert registry.get_driver("github:search") is not None


def test_capability_state_transitions_are_validated():
    registry = CapabilityRegistry()
    record = registry.register_definition(
        CapabilityDefinition(
            id="discovered.only",
            name="discovered.only",
            description="metadata only",
        )
    )

    with pytest.raises(ValueError, match="Invalid capability state transition: DISCOVERED -> ENABLED"):
        registry.set_state(record.id, CapabilityState.ENABLED)

    driver = EchoDriver(CapabilityDefinition(
        id="local.echo",
        name="local.echo",
        description="Echo",
    ))
    registry.register_capability(driver)
    registry.set_state("local.echo", CapabilityState.DISABLED)
    registry.set_state("local.echo", CapabilityState.ENABLED)

    assert registry.get("local.echo").state is CapabilityState.ENABLED


@pytest.mark.asyncio
async def test_disabled_mcp_capability_is_not_reenabled_by_health_check():
    class HealthySessionProvider:
        def get_raw_session(self, _server_name):
            return object()

    registry = CapabilityRegistry()
    runtime = CapabilityRuntime(registry=registry)
    definition = CapabilityDefinition(
        id="github:search",
        name="github:search",
        description="Search GitHub",
        source="MCP",
        execution_kind="MCP",
        metadata={"mcp_server": "github", "mcp_tool_name": "search"},
    )
    runtime.register_capability(
        McpCapabilityDriver(definition, HealthySessionProvider())
    )
    registry.set_state("github:search", CapabilityState.DISABLED)

    assert await runtime.check_health() == HealthStatus.HEALTHY
    assert registry.get("github:search").state is CapabilityState.DISABLED
