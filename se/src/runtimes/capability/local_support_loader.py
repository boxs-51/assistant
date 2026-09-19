"""Manifest discovery and lazy materialization for server Skills and Agents."""
from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from ...domain.schemas.agent import AgentDefinition
from .contracts.context import CapabilityExecutionContext
from .contracts.definition import (
    CapabilityDefinition,
    CapabilityExecutionMode,
    CapabilityKind,
)
from .contracts.implementation import (
    CapabilityExecutionLocation,
    CapabilityImplementation,
    CapabilityImplementationState,
    CapabilityOwnerType,
)
from .drivers.agent_driver import AgentCapabilityDriver
from .drivers.base import BaseCapabilityDriver


class LazyAgentCapabilityDriver(BaseCapabilityDriver):
    def __init__(self, definition: CapabilityDefinition, loader: "LocalSupportLoader"):
        super().__init__(definition)
        self._loader = loader

    async def execute(
        self, context: CapabilityExecutionContext, arguments: Mapping[str, Any]
    ) -> Any:
        agent = self._loader.load_agent(self.definition.capability_id)
        delegate = AgentCapabilityDriver(
            self.definition,
            agent,
            self._loader.container.agent_runtime,
            execution_id_factory=getattr(
                self._loader.container, "agent_execution_id_factory", None
            ),
        )
        return await delegate.execute(context, arguments)


class LocalSupportLoader:
    """Discover lightweight manifests at startup and load content on first use."""

    def __init__(self, container, skills_dir: Path, agents_dir: Path):
        self.container = container
        self.skills_dir = skills_dir.resolve()
        self.agents_dir = agents_dir.resolve()
        self._skill_manifests: dict[str, tuple[Path, dict[str, Any]]] = {}
        self._agent_manifests: dict[str, tuple[Path, dict[str, Any]]] = {}
        self._loaded_skills: set[str] = set()
        self._loaded_agents: set[str] = set()
        self._lock = RLock()

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not str(data.get("name") or "").strip():
            raise ValueError(f"Invalid support manifest: {path}")
        return data

    @staticmethod
    def _manifest_files(root: Path):
        if not root.is_dir():
            return ()
        return tuple(sorted(root.glob("*/manifest.json")))

    @staticmethod
    def _metadata(_manifest_path: Path, manifest: dict[str, Any], kind: str):
        return {
            "kind": kind,
            "server_managed": True,
            "lazy": True,
            "loaded": False,
            "required_permissions": list(manifest.get("required_permissions", [])),
        }

    def discover(self) -> dict[str, list[str]]:
        catalog = self.container.capability_runtime.catalog
        for path in self._manifest_files(self.skills_dir):
            manifest = self._read_manifest(path)
            name = str(manifest["name"])
            definition = CapabilityDefinition(
                id=name,
                version=str(manifest.get("version", "1.0")),
                name=name,
                description=str(manifest.get("description", name)),
                execution_kind="SKILL",
                kind=CapabilityKind.SKILL,
                execution_mode=CapabilityExecutionMode.CONTEXT_ONLY,
                require_auth=bool(manifest.get("require_auth", False)),
                required_scopes=list(manifest.get("required_scopes", [])),
                metadata=self._metadata(path, manifest, "SKILL"),
            )
            catalog.register_definition(definition)
            self.container.capability_runtime.registry.register_definition(definition)
            self._skill_manifests[name] = (path, manifest)

        pending_agents: list[tuple[Path, dict[str, Any], CapabilityDefinition]] = []
        for path in self._manifest_files(self.agents_dir):
            manifest = self._read_manifest(path)
            name = str(manifest["name"])
            definition = CapabilityDefinition(
                id=name,
                name=name,
                description=str(manifest.get("goal", name)),
                input_schema={
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                    "required": ["prompt"],
                },
                execution_kind="AGENT",
                kind=CapabilityKind.AGENT,
                execution_mode=CapabilityExecutionMode.LONG_RUNNING,
                require_auth=bool(manifest.get("require_auth", False)),
                required_scopes=list(manifest.get("required_scopes", [])),
                metadata={
                    **self._metadata(path, manifest, "AGENT"),
                    "agent_summary": self._agent_from_manifest(manifest, instruction="").model_dump(mode="json"),
                },
            )
            catalog.register_definition(definition)
            self._agent_manifests[name] = (path, manifest)
            pending_agents.append((path, manifest, definition))

        for _path, manifest, definition in pending_agents:
            driver = LazyAgentCapabilityDriver(definition, self)
            self.container.capability_runtime.register_capability(driver)
            implementation_id = f"server:agent:{definition.capability_id}"
            if not catalog.contains_implementation(implementation_id):
                implementation = CapabilityImplementation.from_definition(
                    definition,
                    implementation_id=implementation_id,
                    location=CapabilityExecutionLocation.SERVER,
                    driver_kind="LAZY_AGENT_RUNTIME",
                    owner_type=CapabilityOwnerType.SYSTEM,
                    metadata={"kind": "AGENT", "lazy": True},
                )
                catalog.register_implementation(implementation)
                catalog.transition_implementation(
                    implementation_id, CapabilityImplementationState.ENABLED
                )
            self.container.capability_runtime.driver_registry.bind(
                implementation_id, driver, replace=True
            )

        return {
            "skills": sorted(self._skill_manifests),
            "agents": sorted(self._agent_manifests),
        }

    @staticmethod
    def _instruction(path: Path, manifest: dict[str, Any]) -> str:
        inline = manifest.get("instruction")
        if isinstance(inline, str) and inline.strip():
            return inline.strip()
        relative = str(manifest.get("instruction_file") or "instruction.md")
        target = (path.parent / relative).resolve()
        if path.parent.resolve() not in target.parents:
            raise ValueError(f"Instruction path escapes manifest directory: {path}")
        return target.read_text(encoding="utf-8").strip()

    @staticmethod
    def _agent_from_manifest(
        manifest: dict[str, Any], *, instruction: str
    ) -> AgentDefinition:
        return AgentDefinition(
            name=str(manifest["name"]),
            goal=str(manifest.get("goal", manifest["name"])),
            instruction=instruction,
            tools=list(manifest.get("tools", [])),
            skills=list(manifest.get("skills", [])),
            workflow_definition=manifest.get("workflow_definition"),
            memory_config=manifest.get("memory_config") or {},
        )

    def load_skill(self, skill_id: str) -> CapabilityDefinition:
        with self._lock:
            path, manifest = self._skill_manifests[skill_id]
            definition = self.container.capability_runtime.catalog.get_definition(skill_id)
            if skill_id in self._loaded_skills:
                return definition
            loaded = definition.model_copy(
                update={
                    "metadata": {
                        **definition.metadata,
                        "instruction": self._instruction(path, manifest),
                        "loaded": True,
                    }
                }
            )
            self.container.capability_runtime.catalog.register_definition(
                loaded, allow_update=True
            )
            self.container.capability_runtime.registry.register_definition(loaded)
            self._loaded_skills.add(skill_id)
            return loaded

    def load_agent(self, agent_id: str) -> AgentDefinition | None:
        with self._lock:
            existing = self.container.agent_registry.get_loaded(agent_id)
            if existing is not None:
                return existing
            entry = self._agent_manifests.get(agent_id)
            if entry is None:
                return None
            path, manifest = entry
            for skill_id in manifest.get("skills", []):
                if skill_id in self._skill_manifests:
                    self.load_skill(skill_id)
            agent = self._agent_from_manifest(
                manifest, instruction=self._instruction(path, manifest)
            )
            self.container.agent_registry.register(agent)
            self._loaded_agents.add(agent_id)
            return agent

    def list_agent_summaries(self, identity=None) -> list[AgentDefinition]:
        result = []
        authorization = (
            getattr(self.container, "authorization_service", None)
            or self.container.capability_runtime.authorization
        )
        catalog = self.container.capability_runtime.catalog
        for agent_id, (_path, manifest) in sorted(self._agent_manifests.items()):
            definition = catalog.get_definition(agent_id)
            if identity is not None and not authorization.is_allowed(identity, definition):
                continue
            loaded = self.container.agent_registry.get_loaded(agent_id)
            result.append(loaded or self._agent_from_manifest(manifest, instruction=""))
        return result

    def is_loaded(self, capability_id: str) -> bool:
        return capability_id in self._loaded_agents or capability_id in self._loaded_skills


__all__ = ["LazyAgentCapabilityDriver", "LocalSupportLoader"]
