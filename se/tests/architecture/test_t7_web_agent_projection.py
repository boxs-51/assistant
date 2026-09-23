from __future__ import annotations

import asyncio
import json
from pathlib import Path

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent import AgentDefinition
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.agent.capabilities import RegistryAgentCapabilityResolver
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.drivers.python_driver import PythonCapabilityDriver
from se.src.runtimes.capability.registry import CapabilityRegistry


WEB_CAPABILITIES = (
    "web.search",
    "web.read",
    "web.read_many",
)


def _manifest():
    root = Path(__file__).resolve().parents[3]
    path = root / "agents" / "v1" / "web-researcher" / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_web_researcher_manifest_uses_only_logical_web_capabilities():
    manifest = _manifest()

    assert manifest["tools"] == list(WEB_CAPABILITIES)
    assert "web_tool" not in manifest["tools"]


def test_web_researcher_capability_projection_hides_physical_umbrella():
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
        for capability_id in WEB_CAPABILITIES:
            definition = CapabilityDefinition(
                id=capability_id,
                name=capability_id,
                description=capability_id,
                input_schema={
                    "type": "object",
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

        # Even if a physical umbrella driver exists elsewhere in the global
        # registry, the Agent manifest must not project it into model context.
        physical = CapabilityDefinition(
            id="web_tool",
            name="web_tool",
            description="physical implementation",
            input_schema={"type": "object"},
            source="LOCAL",
            execution_kind="PYTHON",
        )
        registry.register_capability(
            PythonCapabilityDriver(physical, lambda **kwargs: kwargs)
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

        resolved = await resolver.resolve(
            agent_id=agent.name,
            identity=None,
        )

        assert tuple(item.capability_id for item in resolved) == WEB_CAPABILITIES
        assert all(item.name != "web_tool" for item in resolved)

    asyncio.run(scenario())
