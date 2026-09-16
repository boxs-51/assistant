from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts.context_assembly import AgentContextAssembly
from .contracts.inference import InferenceMessage, InferenceToolDefinition


class DefaultAgentContextAssembler:
    """Canonical Phase 6.11 system/capability/conversation assembly."""

    def __init__(self, system_prompt_provider, capability_resolver) -> None:
        self._system_prompt_provider = system_prompt_provider
        self._capability_resolver = capability_resolver

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
            constraints=context.limits.model_dump(mode="json"),
            messages=messages,
            tools=tools,
        )


__all__ = ["DefaultAgentContextAssembler"]
