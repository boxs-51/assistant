# se/src/runtimes/agent/capabilities.py

from __future__ import annotations

from .contracts.context_assembly import AgentCapabilityView
from .contracts.policy import AgentToolPolicy, PolicyDecision
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
