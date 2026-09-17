import asyncio

from se.src.agent.registry import AgentRegistry
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.assembly import DefaultAgentContextAssembler
from se.src.runtimes.agent.capabilities import (
    RegistryAgentCapabilityResolver,
    RegistryAgentSkillResolver,
)
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.system_prompt import DefaultAgentSystemPromptProvider
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.application.policy.authorization import AuthorizationService


def test_assigned_skills_are_resolved_only_when_agent_context_is_assembled():
    async def scenario():
        agents = AgentRegistry()
        agents.register(
            AgentDefinition(
                name="reviewer",
                goal="Review code",
                instruction="Be concise.",
                skills=["review-skill"],
            )
        )
        registry = CapabilityRegistry()
        catalog = CapabilityCatalog()
        catalog.register_definition(
            CapabilityDefinition(
                id="review-skill",
                name="review-skill",
                description="Review workflow",
                execution_kind="SKILL",
                metadata={
                    "kind": "SKILL",
                    "instruction": "Inspect correctness before style.",
                },
            )
        )
        catalog.register_definition(
            CapabilityDefinition(
                id="unused-skill",
                name="unused-skill",
                description="Must not leak",
                execution_kind="SKILL",
                metadata={"kind": "SKILL", "instruction": "UNUSED SECRET"},
            )
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
            RegistryAgentSkillResolver(
                agent_registry=agents,
                capability_catalog=catalog,
            ),
        )
        context = AgentExecutionContext.create(
            execution_id="exec-skill",
            agent_id="reviewer",
            session_id="session-skill",
            correlation_id="correlation-skill",
            identity=Identity(user_id="user-1", auth_type="jwt"),
            limits=AgentExecutionLimits(),
            agent=agents.get("reviewer"),
        )

        assembled = await assembler.assemble(context=context, prior_messages=[])

        assert [item.skill_id for item in assembled.skills] == ["review-skill"]
        assert "Inspect correctness before style." in assembled.system_prompt.content
        assert "UNUSED SECRET" not in assembled.system_prompt.content
        assert assembled.system_prompt.source.endswith("+skills")

    asyncio.run(scenario())
