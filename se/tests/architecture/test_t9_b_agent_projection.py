from __future__ import annotations

import asyncio
import json
from pathlib import Path

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.agent.capabilities import RegistryAgentCapabilityResolver
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from se.src.runtimes.capability.registry import CapabilityRegistry


COMMAND_REVIEWER_CAPABILITIES = (
    "terminal.run",
    "terminal.launch",
    "file.read",
    "file.search",
    "file.write",
    "file.append",
    "file.replace",
    "glob.find",
)


def _manifest():
    root = Path(__file__).resolve().parents[3]
    path = root / "agents" / "v1" / "command-reviewer" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_command_reviewer_manifest_uses_only_t9_b_logical_capabilities():
    manifest = _manifest()

    assert manifest["tools"] == [*COMMAND_REVIEWER_CAPABILITIES, "skill.load"]
    assert not {"terminal_tool", "file_tool", "find_by_glob"}.intersection(
        manifest["tools"]
    )


def test_command_reviewer_projection_hides_physical_roots():
    async def scenario():
        manifest = _manifest()

        agents = AgentRegistry()
        agent = AgentDefinition(
            name=manifest["name"],
            goal=manifest["goal"],
            instruction="test",
            tools=list(manifest["tools"]),
            skills=list(manifest.get("skills", [])),
        )
        agents.register(agent)

        registry = CapabilityRegistry()
        for capability_id in COMMAND_REVIEWER_CAPABILITIES:
            definition = CapabilityDefinition(
                id=capability_id,
                name=capability_id,
                description=capability_id,
                input_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
                source="LOCAL",
                execution_kind="PYTHON",
            )
            registry.register_capability(
                PythonCapabilityDriver(
                    definition,
                    lambda **kwargs: kwargs,
                )
            )

        for physical_root in ("terminal_tool", "file_tool", "find_by_glob"):
            definition = CapabilityDefinition(
                id=physical_root,
                name=physical_root,
                description="physical implementation",
                input_schema={"type": "object"},
                source="LOCAL",
                execution_kind="PYTHON",
            )
            registry.register_capability(
                PythonCapabilityDriver(
                    definition,
                    lambda **kwargs: kwargs,
                )
            )

        policy = RegistryAgentToolPolicy(
            agents,
            registry,
            AuthorizationService(),
        )
        resolver = RegistryAgentCapabilityResolver(
            agent_registry=agents,
            capability_registry=registry,
            tool_policy=policy,
        )

        # Unauthenticated, guest and write-only principals must not project
        # executable terminal tools into an Agent's available tool context.
        for identity in (
            None,
            Identity(auth_type="guest"),
            Identity(auth_type="jwt", permissions=["admin:write"]),
        ):
            resolved = await resolver.resolve(
                agent_id=agent.name,
                identity=identity,
            )
            visible_ids = tuple(item.capability_id for item in resolved)
            assert visible_ids == COMMAND_REVIEWER_CAPABILITIES[2:]
            assert not {"terminal.run", "terminal.launch"}.intersection(visible_ids)
            assert not {
                "terminal_tool",
                "file_tool",
                "find_by_glob",
            }.intersection(visible_ids)

        # An explicitly terminal-authorized principal retains both canonical
        # logical IDs; physical implementation roots remain undiscoverable.
        privileged = await resolver.resolve(
            agent_id=agent.name,
            identity=Identity(auth_type="jwt", permissions=["terminal:execute"]),
        )
        privileged_ids = tuple(item.capability_id for item in privileged)
        assert privileged_ids == COMMAND_REVIEWER_CAPABILITIES
        assert not {
            "terminal_tool",
            "file_tool",
            "find_by_glob",
        }.intersection(privileged_ids)

    asyncio.run(scenario())
