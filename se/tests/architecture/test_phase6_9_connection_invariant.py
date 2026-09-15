import asyncio

import pytest

from src.domain.schemas.identity import Identity
from src.runtimes.agent.adapters.tool import CapabilityToolExecutionAdapter
from src.runtimes.agent.contracts.context import AgentExecutionContext
from src.runtimes.agent.contracts.tool import ToolExecutionRequest
from src.runtimes.agent.contracts.policy import (
    AgentExecutionPolicy,
    AgentToolPolicy,
    PolicyDecision,
)
from src.domain.schemas.agent_execution import AgentExecutionLimits
from src.runtimes.capability.catalog import CapabilityCatalog
from src.runtimes.capability.contracts.definition import CapabilityDefinition
from src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from src.runtimes.capability.policy import CapabilityRoutingPolicy
from src.runtimes.capability.runtime import CapabilityRuntime
from src.runtimes.connection.registry import ConnectionRegistry
from src.runtimes.connection.realtime import RealtimeMultiplexer
from src.runtimes.capability.drivers.base import BaseCapabilityDriver


class FakeServerDriver(BaseCapabilityDriver):
    async def execute(self, context, arguments):
        return {"ok": True, "connection_id": context.metadata.get("connection_id")}


class AllowToolPolicy:
    def is_visible(self, *, agent_id, capability_id):
        return True

    def authorize(self, *, identity, agent_id, capability_id):
        return PolicyDecision.ALLOW


class AllowExecutionPolicy:
    def check_tool_call(self, context, request):
        return PolicyDecision.ALLOW


def _context(connection_id="conn-a"):
    return AgentExecutionContext.create(
        execution_id="exec-1",
        agent_id="agent-1",
        session_id="sess-1",
        correlation_id="corr-1",
        identity=Identity(
            user_id="user-1",
            session_id="sess-1",
            auth_type="jwt",
        ),
        limits=AgentExecutionLimits(),
        connection_id=connection_id,
    )


def test_tool_request_must_match_agent_execution_connection():
    adapter = CapabilityToolExecutionAdapter(
        capability_runtime=None,
        tool_policy=AllowToolPolicy(),
        execution_policy=AllowExecutionPolicy(),
    )
    context = _context("conn-a")
    request = ToolExecutionRequest(
        execution_id=context.execution_id,
        iteration=1,
        invocation_id="inv-1",
        tool_call_id="call-1",
        capability_id="echo",
        connection_id="conn-b",
        arguments={},
    )

    with pytest.raises(ValueError, match="connection_id"):
        asyncio.run(adapter.execute(context, request))


def test_runtime_rejects_conflicting_explicit_and_metadata_connection():
    catalog = CapabilityCatalog()
    definition = CapabilityDefinition(
        id="server.echo",
        name="server.echo",
        description="Echo",
        input_schema={"type": "object"},
    )
    catalog.register_definition(definition)
    driver = FakeServerDriver(definition)
    runtime_registry = __import__(
        "src.runtimes.capability.registry",
        fromlist=["CapabilityRegistry"],
    ).CapabilityRegistry()
    runtime_registry.register_capability(driver)
    runtime = CapabilityRuntime(
        registry=runtime_registry,
        catalog=catalog,
        routing_policy=CapabilityRoutingPolicy(),
    )

    identity = Identity(user_id="user-1", auth_type="jwt", session_id="sess-1")
    with pytest.raises(ValueError, match="connection_id"):
        asyncio.run(
            runtime.execute_capability(
                "server.echo",
                {},
                identity,
                connection_id="conn-a",
                metadata={"connection_id": "conn-b"},
            )
        )