# se/src/runtimes/agent/capabilities.py

from __future__ import annotations

from ...application.policy.authorization import AuthorizationService
from .contracts.context_assembly import AgentCapabilityView, AgentSkillView
from .contracts.policy import AgentToolPolicy, PolicyDecision
from .contracts.skills import SkillDescriptor
from ..capability.catalog import CapabilityNotFoundError


class RegistryAgentCapabilityResolver:
    def __init__(
        self,
        *,
        agent_registry,
        capability_registry,
        tool_policy: AgentToolPolicy,
        capability_catalog=None,
    ) -> None:
        self._agents = agent_registry
        self._capabilities = capability_registry
        self._policy = tool_policy
        self._catalog = capability_catalog

    async def resolve(
        self,
        *,
        agent_id: str,
        identity,
    ) -> tuple[AgentCapabilityView, ...]:
        agent = self._agents.get(agent_id)
        if agent is None:
            return ()

        result: list[AgentCapabilityView] = []

        for capability_id in agent.tools or []:
            if not self._policy.is_visible(
                agent_id=agent_id,
                capability_id=capability_id,
            ):
                continue

            if self._policy.authorize(
                identity=identity,
                agent_id=agent_id,
                capability_id=capability_id,
            ) is not PolicyDecision.ALLOW:
                continue

            record = self._capabilities.get(capability_id)
            definition = (
                record.definition
                if record is not None and record.executable
                else self._catalog_definition(capability_id)
            )
            if definition is None:
                continue

            result.append(
                AgentCapabilityView(
                    capability_id=capability_id,
                    name=definition.name,
                    description=definition.description,
                    parameters=dict(definition.parameters or {}),
                )
            )

        return tuple(result)

    def _catalog_definition(self, capability_id):
        if self._catalog is None:
            return None
        try:
            definition = self._catalog.get_definition(capability_id)
            implementations = self._catalog.list_implementations(
                capability_id,
                routable_only=True,
            )
        except (KeyError, CapabilityNotFoundError):
            return None
        return definition if implementations else None


class RegistryAgentSkillResolver:
    """Resolve only the skills assigned to an agent when its context is built."""

    def __init__(self, *, agent_registry, capability_catalog, authorization=None) -> None:
        self._agents = agent_registry
        self._catalog = capability_catalog
        self._authorization = authorization or AuthorizationService()

    async def list_descriptors(self, *, identity) -> tuple[SkillDescriptor, ...]:
        """Expose authorized trusted V2 descriptors without reading Skill bodies."""
        result: list[SkillDescriptor] = []
        for definition in self._catalog.list_definitions():
            if str(definition.metadata.get("kind", "")).upper() != "SKILL":
                continue
            if definition.metadata.get("server_managed") is not True:
                continue
            if definition.metadata.get("lazy") is not True:
                continue
            if not self._authorization.is_allowed(identity, definition):
                continue
            try:
                result.append(SkillDescriptor.from_definition(definition))
            except ValueError:
                # Legacy/non-V2 definitions are not trusted V2 descriptor sources.
                continue
        return tuple(result)

    async def list_available(self, *, identity) -> tuple[AgentSkillView, ...]:
        """Compatibility projection of authorized trusted Skill descriptors."""
        descriptors = await self.list_descriptors(identity=identity)
        return tuple(
            AgentSkillView(
                skill_id=descriptor.skill_id,
                name=descriptor.name,
                description=descriptor.description,
                instruction="",
                version=descriptor.version,
            )
            for descriptor in descriptors
        )

    async def resolve(self, *, agent_id: str, identity) -> tuple[AgentSkillView, ...]:
        agent = self._agents.get(agent_id)
        if agent is None:
            return ()

        result: list[AgentSkillView] = []
        for skill_id in agent.skills or []:
            try:
                definition = self._catalog.get_definition(skill_id)
            except (KeyError, CapabilityNotFoundError):
                continue
            if str(definition.metadata.get("kind", "")).upper() != "SKILL":
                continue
            if not self._authorization.is_allowed(identity, definition):
                continue
            instruction = definition.metadata.get("instruction")
            if not isinstance(instruction, str) or not instruction.strip():
                continue
            result.append(
                AgentSkillView(
                    skill_id=skill_id,
                    name=definition.name,
                    description=definition.description,
                    instruction=instruction.strip(),
                    version=definition.version,
                )
            )
        return tuple(result)
