from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts.context_assembly import AgentContextAssembly, AgentSystemPrompt
from .contracts.inference import InferenceMessage, InferenceToolDefinition
from .contracts.selection import (
    CapabilitySelectionCandidate,
    CapabilitySelectionContext,
    CapabilityWorkingSet,
)
from .contracts.skills import (
    ActiveSkill,
    ActiveSkillSet,
    SkillActivationSource,
    SkillDescriptor,
)


class DefaultAgentContextAssembler:
    """Canonical Agent context assembly with optional DCS selection."""

    def __init__(
        self,
        system_prompt_provider,
        capability_resolver,
        skill_resolver=None,
        *,
        selector=None,
    ) -> None:
        self._system_prompt_provider = system_prompt_provider
        self._capability_resolver = capability_resolver
        self._skill_resolver = skill_resolver
        self._selector = selector

    async def _active_assigned_skills(
        self,
        *,
        context,
        resolved_skills,
    ) -> ActiveSkillSet:
        assigned_ids = tuple(getattr(context.agent, "skills", ()) or ())
        if not assigned_ids or self._skill_resolver is None:
            return ActiveSkillSet()

        trusted_descriptors: dict[str, SkillDescriptor] = {}
        descriptor_loader = getattr(self._skill_resolver, "list_descriptors", None)
        if callable(descriptor_loader):
            descriptors = await descriptor_loader(identity=context.identity)
            trusted_descriptors = {
                item.skill_id: item
                for item in descriptors
                if item.skill_id in assigned_ids
            }

        resolved_by_id = {item.skill_id: item for item in resolved_skills}
        active: list[ActiveSkill] = []
        for skill_id in assigned_ids:
            descriptor = trusted_descriptors.get(skill_id)
            if descriptor is None:
                view = resolved_by_id.get(skill_id)
                if view is None:
                    continue
                descriptor = SkillDescriptor(
                    skill_id=view.skill_id,
                    version=view.version,
                    name=view.name,
                    description=view.description,
                    provenance="LEGACY_ASSIGNED",
                )
            active.append(
                ActiveSkill(
                    descriptor=descriptor,
                    source=SkillActivationSource.ASSIGNED,
                )
            )
        return ActiveSkillSet(skills=tuple(active))

    @staticmethod
    def _explicit_requested_capability_ids(context) -> tuple[str, ...]:
        raw = context.metadata.get("dcs_requested_capability_ids", ())
        if not isinstance(raw, (tuple, list, set, frozenset)):
            return ()
        return tuple(
            dict.fromkeys(
                item.strip()
                for item in raw
                if isinstance(item, str) and item.strip()
            )
        )

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
        eligible_capabilities = tuple(
            await self._capability_resolver.resolve(
                agent_id=context.agent_id,
                identity=context.identity,
            )
        )

        resolved_skills = ()
        if self._skill_resolver is not None:
            resolved_skills = tuple(
                await self._skill_resolver.resolve(
                    agent_id=context.agent_id,
                    identity=context.identity,
                )
            )

        conversation = tuple(
            InferenceMessage.model_validate(item)
            for item in prior_messages
            if item.get("role") != "system"
        )
        active_skill_set = await self._active_assigned_skills(
            context=context,
            resolved_skills=resolved_skills,
        )

        if self._selector is None:
            selected_capabilities = eligible_capabilities
            working_set = CapabilityWorkingSet(
                visible_capability_ids=tuple(
                    item.capability_id for item in selected_capabilities
                ),
                active_groups=(),
                reason="LEGACY_FULL_ELIGIBLE",
                provenance=("LEGACY_COMPATIBILITY",),
                revision=max(1, int(context.iteration or 0)),
            )
        else:
            selection = self._selector.select(
                CapabilitySelectionContext(
                    owner_user_id=(
                        str(context.identity.user_id)
                        if context.identity.user_id
                        else None
                    ),
                    agent_id=context.agent_id,
                    execution_id=context.execution_id,
                    iteration=max(1, int(context.iteration or 0)),
                    messages=conversation,
                    eligible_capabilities=tuple(
                        CapabilitySelectionCandidate(
                            capability_id=item.capability_id,
                            name=item.name,
                            description=item.description,
                        )
                        for item in eligible_capabilities
                    ),
                    explicit_requested_capability_ids=(
                        self._explicit_requested_capability_ids(context)
                    ),
                    active_skill_set=active_skill_set,
                )
            )
            selected_ids = set(selection.working_set.visible_capability_ids)
            eligible_ids = {
                item.capability_id for item in eligible_capabilities
            }
            if not selected_ids.issubset(eligible_ids):
                raise ValueError(
                    "DCS selector returned capability outside eligible Agent envelope."
                )
            selected_capabilities = tuple(
                item
                for item in eligible_capabilities
                if item.capability_id in selected_ids
            )
            working_set = selection.working_set
            active_skill_set = selection.active_skill_set

        active_skill_ids = {
            item.descriptor.skill_id
            for item in active_skill_set.skills
        }
        skills = tuple(
            item
            for item in resolved_skills
            if item.skill_id in active_skill_ids
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

        if self._skill_resolver is not None and any(
            item.capability_id == "skill.load"
            for item in selected_capabilities
        ):
            available = await self._skill_resolver.list_available(
                identity=context.identity,
            )
            if available:
                summaries = "\n".join(
                    f"- {skill.skill_id}: {skill.description}"
                    for skill in available
                )
                system_prompt = AgentSystemPrompt(
                    content=(
                        f"{system_prompt.content}\n\n"
                        "[AVAILABLE SKILLS]\n"
                        f"{summaries}\n"
                        "Call skill.load with a skill_id when its instructions are needed. "
                        "Use the returned instruction; do not assume a description "
                        "contains the skill body."
                    ),
                    source=f"{system_prompt.source}+skill_catalog",
                    version=system_prompt.version,
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
            for item in selected_capabilities
        )
        return AgentContextAssembly(
            system_prompt=system_prompt,
            capabilities=selected_capabilities,
            skills=skills,
            constraints=context.limits.model_dump(mode="json"),
            messages=messages,
            tools=tools,
            working_set=working_set,
            active_skill_set=active_skill_set,
        )


__all__ = ["DefaultAgentContextAssembler"]
