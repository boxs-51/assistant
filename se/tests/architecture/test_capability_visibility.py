from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.domain.schemas.tool import GatewayToolDefinition
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import (
    CapabilityDefinition,
    CapabilityKind,
)
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.tool.registry import ToolRegistry
from se.src.transport.gateway.api.v1.agent_router import list_agents
from se.src.transport.gateway.api.v1.capability_router import list_capabilities
from se.src.transport.gateway.api.v1.tool_router import list_tools


def _identity(*permissions):
    return Identity(auth_type="guest", permissions=list(permissions))


@pytest.mark.asyncio
async def test_catalog_agent_and_tool_lists_hide_unauthorized_support():
    catalog = CapabilityCatalog()
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        catalog=catalog,
        authorization=AuthorizationService(),
    )
    agent_registry = AgentRegistry()
    tool_registry = ToolRegistry(runtime.registry)
    required = {"required_permissions": ["support.private"]}
    for name, kind in (
        ("private-tool", CapabilityKind.TOOL),
        ("private-skill", CapabilityKind.SKILL),
        ("private-agent", CapabilityKind.AGENT),
    ):
        catalog.register_definition(CapabilityDefinition(
            id=name,
            name=name,
            description=name,
            kind=kind,
            metadata={"kind": kind.value, **required},
        ))
    tool_registry.register(GatewayToolDefinition(
        name="private-tool", description="private", parameters={"type": "object"}
    ))
    agent_registry.register(AgentDefinition(
        name="private-agent", goal="private", instruction="private"
    ))
    container = SimpleNamespace(
        capability_runtime=runtime,
        authorization_service=runtime.authorization,
        agent_registry=agent_registry,
        tool_registry=tool_registry,
        support_loader=None,
    )

    assert await list_capabilities(None, _identity(), container) == []
    assert await list_agents(container, _identity()) == []
    assert await list_tools(_identity(), container) == []

    permitted = _identity("support.private")
    assert len(await list_capabilities(None, permitted, container)) == 3
    assert [item.name for item in await list_agents(container, permitted)] == [
        "private-agent"
    ]
    assert [item.name for item in await list_tools(permitted, container)] == [
        "private-tool"
    ]
