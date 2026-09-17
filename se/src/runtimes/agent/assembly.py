from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts.context_assembly import AgentContextAssembly, AgentSystemPrompt
from .contracts.inference import InferenceMessage, InferenceToolDefinition


class DefaultAgentContextAssembler:
    """Canonical Phase 6.11 system/capability/conversation assembly."""

    def __init__(self, system_prompt_provider, capability_resolver, skill_resolver=None) -> None:
        self._system_prompt_provider = system_prompt_provider
        self._capability_resolver = capability_resolver
        self._skill_resolver = skill_resolver

    async def assemble(
        self,
        *,
        context,
        prior_messages: Sequence[Mapping[str, Any]],
    ) -> AgentContextAssembly:
        system_prompt = await self._system_prompt_provider.build(
            agent=context.agent,
            context=context,
        )
        capabilities = tuple(
            await self._capability_resolver.resolve(
                agent_id=context.agent_id,
                identity=context.identity,
            )
        )
        skills = ()
        if self._skill_resolver is not None:
            skills = tuple(
                await self._skill_resolver.resolve(
                    agent_id=context.agent_id,
                    identity=context.identity,
                )
            )
        if skills:
            skill_sections = []
            for skill in skills:
                header = f"[SKILL: {skill.name}]"
                if skill.description:
                    header += f"\nDescription: {skill.description}"
                skill_sections.append(f"{header}\n{skill.instruction}")
            system_prompt = AgentSystemPrompt(
                content="\n\n".join((system_prompt.content, *skill_sections)),
                source=f"{system_prompt.source}+skills",
                version=system_prompt.version,
            )
        conversation = tuple(
            InferenceMessage.model_validate(item)
            for item in prior_messages
            if item.get("role") != "system"
        )
        messages = (
            InferenceMessage(role="system", content=system_prompt.content),
            *conversation,
        )
        tools = tuple(
            InferenceToolDefinition(
                name=item.name,
                description=item.description,
                parameters=dict(item.parameters),
            )
            for item in capabilities
        )
        return AgentContextAssembly(
            system_prompt=system_prompt,
            capabilities=capabilities,
            skills=skills,
            constraints=context.limits.model_dump(mode="json"),
            messages=messages,
            tools=tools,
        )


__all__ = ["DefaultAgentContextAssembler"]
