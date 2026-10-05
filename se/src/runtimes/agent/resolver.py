from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentSelection:
    """Compatibility-normalized Agent selection before registry resolution."""

    agent_id: str | None
    source: str


@dataclass(frozen=True)
class AgentResolution:
    """Resolved Agent definition plus the request source that selected it."""

    agent_id: str
    agent: Any
    source: str


class AgentResolutionError(LookupError):
    """Deterministic failure for an explicitly/default-selected unknown Agent."""

    code = "AGENT_NOT_FOUND"

    def __init__(self, *, agent_id: str, source: str) -> None:
        self.agent_id = agent_id
        self.source = source
        super().__init__(f"Agent '{agent_id}' is unavailable.")


class AgentResolver:
    """Canonical AOS-1 Agent selection and registry-resolution authority."""

    def __init__(self, agent_registry) -> None:
        self._agent_registry = agent_registry

    @staticmethod
    def _normalized_id(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        return value or None

    def select(self, request_body: Mapping[str, Any]) -> AgentSelection:
        explicit_id = self._normalized_id(request_body.get("agent_id"))
        if explicit_id is not None:
            return AgentSelection(agent_id=explicit_id, source="EXPLICIT")

        metadata = request_body.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        routing = metadata.get("routing")
        if not isinstance(routing, Mapping):
            routing = {}

        default_id = self._normalized_id(routing.get("default_agent_id"))
        if default_id is not None:
            return AgentSelection(agent_id=default_id, source="DEFAULT")

        return AgentSelection(agent_id=None, source="UNSPECIFIED")

    def resolve(self, selection: AgentSelection) -> AgentResolution | None:
        if selection.agent_id is None:
            return None

        agent = self._agent_registry.get(selection.agent_id)
        if agent is None:
            raise AgentResolutionError(
                agent_id=selection.agent_id,
                source=selection.source,
            )
        return AgentResolution(
            agent_id=selection.agent_id,
            agent=agent,
            source=selection.source,
        )
