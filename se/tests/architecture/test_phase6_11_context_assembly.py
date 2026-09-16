import asyncio

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.context import ContextBuilderAdapter
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.agent.assembly import DefaultAgentContextAssembler
from se.src.runtimes.agent.capabilities import RegistryAgentCapabilityResolver
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.contracts.context_builder import AgentContextRequest
from se.src.runtimes.agent.contracts.inference import InferenceMessage
from se.src.runtimes.agent.system_prompt import DefaultAgentSystemPromptProvider
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime


class _ContextRuntime:
    async def load_context(self, session_id, identity):
        session = type(
            "Session",
            (),
            {
                "messages": [
                    InferenceMessage(role="system", content="stale prompt"),
                    InferenceMessage(role="user", content="history"),
                ]
            },
        )()
        return type("Loaded", (), {"session": session})()


def _register_client(catalog, definition, *, owner_id):
    catalog.register_definition(definition)
    implementation = CapabilityImplementation.from_definition(
        definition,
        implementation_id=f"conn-1:{definition.id}",
        location=CapabilityExecutionLocation.CLIENT,
        driver_kind="REMOTE_CLIENT",
        owner_type=CapabilityOwnerType.CLIENT,
        owner_id=owner_id,
        connection_id="conn-1",
    )
    catalog.register_implementation(implementation)
    catalog.transition_implementation(
        implementation.implementation_id,
        CapabilityImplementationState.ENABLED,
    )


def _setup():
    agents = AgentRegistry()
    agent = AgentDefinition(
        name="test-agent",
        goal="Complete the requested task",
        instruction="Use tools only when required.",
        tools=["client.echo", "client.secret", "missing.tool"],
    )
    agents.register(agent)
    registry = CapabilityRegistry()
    catalog = CapabilityCatalog()
    _register_client(
        catalog,
        CapabilityDefinition(
            id="client.echo",
            name="client.echo",
            description="Echo",
            input_schema={"type": "object"},
        ),
        owner_id="user-1",
    )
    _register_client(
        catalog,
        CapabilityDefinition(
            id="client.secret",
            name="client.secret",
            description="Secret",
            input_schema={"type": "object"},
        ),
        owner_id="other-user",
    )
    policy = RegistryAgentToolPolicy(
        agents,
        registry,
        AuthorizationService(),
        capability_catalog=catalog,
    )
    assembler = DefaultAgentContextAssembler(
        DefaultAgentSystemPromptProvider(),
        RegistryAgentCapabilityResolver(
            agent_registry=agents,
            capability_registry=registry,
            capability_catalog=catalog,
            tool_policy=policy,
        ),
    )
    adapter = ContextBuilderAdapter(
        _ContextRuntime(),
        CapabilityRuntime(registry=registry, catalog=catalog),
        policy,
        context_assembler=assembler,
    )
    context = AgentExecutionContext.create(
        execution_id="exec-1",
        agent_id=agent.name,
        session_id="session-1",
        correlation_id="correlation-1",
        identity=Identity(user_id="user-1", auth_type="jwt"),
        limits=AgentExecutionLimits(max_iterations=3, max_tool_calls=2),
        connection_id="conn-1",
        agent=agent,
        input={"prompt": "current request"},
        metadata={"constitution": "Always report tool results honestly."},
    )
    return adapter, context


def test_phase6_11_builds_one_canonical_system_message_and_filtered_tools():
    async def scenario():
        adapter, context = _setup()
        first = await adapter.build(
            context,
            AgentContextRequest(execution_id="exec-1", iteration=1),
        )
        system_messages = [item for item in first.messages if item.role == "system"]
        assert len(system_messages) == 1
        assert first.messages[0] is system_messages[0]
        prompt = str(first.messages[0].content)
        assert "AGENT CONSTITUTION" in prompt
        assert "Always report tool results honestly" in prompt
        assert "test-agent" in prompt
        assert "Complete the requested task" in prompt
        assert "Use tools only when required" in prompt
        assert "max_iterations: 3" in prompt
        assert [item.name for item in first.tools] == ["client.echo"]
        assert first.metadata["capability_ids"] == ("client.echo",)
        assert first.metadata["system_prompt_version"] == "6.11"

        second = await adapter.build(
            context,
            AgentContextRequest(
                execution_id="exec-1",
                iteration=2,
                prior_messages=[
                    *(item.model_dump(mode="json") for item in first.messages),
                    InferenceMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            {"id": "call-1", "name": "client.echo", "arguments": {}}
                        ],
                    ).model_dump(mode="json"),
                    InferenceMessage(
                        role="tool",
                        name="client.echo",
                        tool_call_id="call-1",
                        content="hello",
                    ).model_dump(mode="json"),
                ],
            ),
        )
        assert sum(item.role == "system" for item in second.messages) == 1
        assert second.messages[0].content == first.messages[0].content
        assert second.messages[-1].role == "tool"
        assert second.messages[-1].content == "hello"

    asyncio.run(scenario())
