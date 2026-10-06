from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.agent_execution import AgentExecutionLimits
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.agent.adapters.policy import RegistryAgentToolPolicy
from se.src.runtimes.agent.assembly import DefaultAgentContextAssembler
from se.src.runtimes.agent.capabilities import (
    RegistryAgentCapabilityResolver,
    RegistryAgentSkillResolver,
)
from se.src.runtimes.agent.contracts.context import AgentExecutionContext
from se.src.runtimes.agent.system_prompt import DefaultAgentSystemPromptProvider
from se.src.runtimes.capability.builtins import register_builtin_support
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.contracts.error import CapabilityError
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.policy import CapabilityRoutingPolicy
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.tool.registry import ToolRegistry
from se.src.transport.gateway.api.v1.capability_router import (
    get_capability,
    list_capabilities,
)


NON_WEB_IDS = (
    "file.read",
    "file.search",
    "file.write",
    "file.append",
    "file.replace",
    "glob.find",
    "terminal.run",
    "terminal.launch",
    "window.list",
    "window.find",
    "window.geometry",
    "window.focus",
    "window.close",
    "window.minimize",
    "window.maximize",
    "window.restore",
    "desktop.screen_info",
    "desktop.mouse_move",
    "desktop.mouse_click",
    "desktop.mouse_drag",
    "desktop.mouse_scroll",
    "desktop.type_text",
    "desktop.press_key",
    "desktop.hotkey",
)

WEB_IDS = (
    "web.search",
    "web.search_many",
    "web.read",
    "web.read_many",
)

SERVER_EXECUTABLE_IDS = (*NON_WEB_IDS, *WEB_IDS)
CLIENT_ONLY_LOGICAL_IDS = ("desktop.screenshot",)
ALL_LOGICAL_IDS = (*SERVER_EXECUTABLE_IDS, *CLIENT_ONLY_LOGICAL_IDS)

PHYSICAL_ROOTS = {
    "file_tool",
    "find_by_glob",
    "terminal_tool",
    "window_tool",
    "desktop_automation",
    "web_tool",
}

T9_E_AGENT_IDS = {
    "agent-command-reviewer",
    "agent-coordinator",
    "agent-web-researcher",
}


PHYSICAL_TOOL_BY_ID = {
    **{capability_id: "file_tool" for capability_id in NON_WEB_IDS[:5]},
    "glob.find": "find_by_glob",
    "terminal.run": "terminal_tool",
    "terminal.launch": "terminal_tool",
    **{
        capability_id: "window_tool"
        for capability_id in NON_WEB_IDS[8:16]
    },
    **{
        capability_id: "desktop_automation"
        for capability_id in NON_WEB_IDS[16:]
    },
    **{capability_id: "web_tool" for capability_id in WEB_IDS},
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _tools_root() -> Path:
    return _repo_root() / "tools" / "v1"


def _runtime_and_container():
    catalog = CapabilityCatalog()
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=catalog,
    )
    tool_registry = ToolRegistry(runtime.registry)
    result = register_local_tools(runtime, tool_registry, _tools_root())

    container = SimpleNamespace(
        capability_runtime=runtime,
        authorization_service=runtime.authorization,
        tool_registry=tool_registry,
    )
    return runtime, catalog, result, container


def _guest() -> Identity:
    return Identity(auth_type="guest")


@pytest.mark.asyncio
async def test_real_projection_exposes_only_logical_ids_and_public_provenance():
    runtime, catalog, result, container = _runtime_and_container()

    assert set(ALL_LOGICAL_IDS).issubset(result)
    assert not PHYSICAL_ROOTS.intersection(result)

    registry_ids = {
        driver.definition.capability_id
        for driver in runtime.registry.get_all_drivers()
    }
    catalog_ids = {
        definition.capability_id
        for definition in catalog.list_definitions()
    }
    model_ids = {
        definition.capability_id
        for definition in await runtime.get_available_capabilities(_guest())
    }

    assert registry_ids == set(SERVER_EXECUTABLE_IDS)
    assert catalog_ids == set(ALL_LOGICAL_IDS)
    assert model_ids == set(SERVER_EXECUTABLE_IDS)
    assert runtime.registry.get_driver("desktop.screenshot") is None
    assert catalog.contains_definition("desktop.screenshot")
    assert not PHYSICAL_ROOTS.intersection(registry_ids)
    assert not PHYSICAL_ROOTS.intersection(catalog_ids)
    assert not PHYSICAL_ROOTS.intersection(model_ids)

    listed = await list_capabilities(None, _guest(), container)
    assert [item.capability_id for item in listed] == sorted(ALL_LOGICAL_IDS)

    for item in listed:
        capability_id = item.capability_id
        assert item.definition["id"] == capability_id
        assert item.definition["name"] == capability_id
        assert "parameters" in item.definition
        assert "physical_tool" not in item.definition["metadata"]
        assert "physical_version" not in item.definition["metadata"]

        if capability_id in CLIENT_ONLY_LOGICAL_IDS:
            assert item.implementations == []
            fetched = await get_capability(
                capability_id,
                _guest(),
                container,
            )
            assert fetched == item
            continue

        assert len(item.implementations) == 1
        implementation = item.implementations[0]
        assert implementation["implementation_id"] == f"server:{capability_id}"
        assert implementation["capability_id"] == capability_id
        assert implementation["location"] == "SERVER"
        assert implementation["state"] == "ENABLED"
        assert implementation["metadata"]["physical_tool"] == (
            PHYSICAL_TOOL_BY_ID[capability_id]
        )
        assert implementation["metadata"]["physical_version"]

        fetched = await get_capability(
            capability_id,
            _guest(),
            container,
        )
        assert fetched == item

    for physical_root in PHYSICAL_ROOTS:
        assert not catalog.contains_definition(physical_root)
        assert runtime.registry.get_driver(physical_root) is None


def test_real_support_loader_projects_logical_agent_tools_only():
    runtime, catalog, _result, container = _runtime_and_container()
    container.agent_registry = AgentRegistry()
    container.agent_runtime = SimpleNamespace()

    discovered = register_builtin_support(container)

    discovered_agents = set(discovered["agents"])
    assert T9_E_AGENT_IDS.issubset(discovered_agents)
    assert container.agent_registry.list_all() == []

    command_reviewer = container.agent_registry.get(
        "agent-command-reviewer"
    )
    coordinator = container.agent_registry.get("agent-coordinator")
    web_researcher = container.agent_registry.get(
        "agent-web-researcher"
    )

    assert command_reviewer is not None
    assert coordinator is not None
    assert web_researcher is not None

    assert command_reviewer.tools == [
        "terminal.run",
        "terminal.launch",
        "file.read",
        "file.search",
        "file.write",
        "file.append",
        "file.replace",
        "glob.find",
        "skill.load",
    ]
    assert web_researcher.tools == [*WEB_IDS, "skill.load"]
    assert coordinator.tools == [
        "terminal.run",
        "terminal.launch",
        "file.read",
        "file.search",
        "file.write",
        "file.append",
        "file.replace",
        "glob.find",
        *WEB_IDS,
        "skill.load",
    ]
    assert coordinator.skills == []
    assert command_reviewer.skills == []
    assert web_researcher.skills == []

    loaded_agents = {
        agent.name: agent
        for agent in container.agent_registry.list_all()
    }
    assert set(loaded_agents) == T9_E_AGENT_IDS

    for agent in loaded_agents.values():
        assert not PHYSICAL_ROOTS.intersection(agent.tools)
        assert not {
            capability_id
            for capability_id in NON_WEB_IDS
            if capability_id.startswith(("window.", "desktop."))
        }.intersection(agent.tools)

    for tool_id in (*command_reviewer.tools, *web_researcher.tools, *coordinator.tools):
        assert catalog.contains_definition(tool_id)
        assert catalog.list_implementations(
            tool_id,
            routable_only=True,
        )

    assert not any(tool_id.startswith("agent-") for tool_id in coordinator.tools)

    summaries = container.support_loader.list_agent_summaries(_guest())
    summary_by_name = {item.name: item for item in summaries}
    assert T9_E_AGENT_IDS.issubset(summary_by_name)
    assert summary_by_name["agent-command-reviewer"].tools == (
        command_reviewer.tools
    )
    assert summary_by_name["agent-web-researcher"].tools == (
        web_researcher.tools
    )
    assert summary_by_name["agent-coordinator"].tools == coordinator.tools


@pytest.mark.asyncio
async def test_coordinator_context_has_direct_tools_and_skill_descriptions():
    runtime, catalog, _result, container = _runtime_and_container()
    container.agent_registry = AgentRegistry()
    container.agent_runtime = SimpleNamespace()
    register_builtin_support(container)

    agent = container.agent_registry.get("agent-coordinator")
    assert agent is not None
    assert not container.support_loader.is_loaded("skill-command-safety")
    assert not container.support_loader.is_loaded("skill-web-research")

    policy = RegistryAgentToolPolicy(
        container.agent_registry,
        runtime.registry,
        runtime.authorization,
        capability_catalog=catalog,
    )
    assembler = DefaultAgentContextAssembler(
        DefaultAgentSystemPromptProvider(),
        RegistryAgentCapabilityResolver(
            agent_registry=container.agent_registry,
            capability_registry=runtime.registry,
            capability_catalog=catalog,
            tool_policy=policy,
        ),
        RegistryAgentSkillResolver(
            agent_registry=container.agent_registry,
            capability_catalog=catalog,
        ),
    )
    context = AgentExecutionContext.create(
        execution_id="coordinator-direct-tools",
        agent_id=agent.name,
        session_id="session-direct-tools",
        correlation_id="correlation-direct-tools",
        identity=_guest(),
        limits=AgentExecutionLimits(),
        agent=agent,
    )
    assembled = await assembler.assemble(context=context, prior_messages=[])

    assert [tool.name for tool in assembled.tools] == agent.tools
    assert assembled.skills == ()
    assert "[AVAILABLE SKILLS]" in assembled.system_prompt.content
    assert "skill-command-safety: Review shell" in assembled.system_prompt.content
    assert "skill-web-research: Collect web" in assembled.system_prompt.content
    assert "Before proposing or running a command" not in assembled.system_prompt.content
    assert "Use the web capability" not in assembled.system_prompt.content
    assert not container.support_loader.is_loaded("skill-command-safety")
    assert not container.support_loader.is_loaded("skill-web-research")
    assert "Do not call another agent" in assembled.system_prompt.content


@pytest.mark.asyncio
async def test_skill_load_reads_only_requested_skill_for_agent():
    runtime, catalog, _result, container = _runtime_and_container()
    runtime.routing_policy = CapabilityRoutingPolicy()
    container.agent_registry = AgentRegistry()
    container.agent_runtime = SimpleNamespace()
    register_builtin_support(container)

    agent = container.agent_registry.get("agent-coordinator")
    assert agent is not None
    assert agent.skills == []
    assert not container.support_loader.is_loaded("skill-command-safety")
    assert not container.support_loader.is_loaded("skill-web-research")

    result = await runtime.execute_capability(
        capability_id="skill.load",
        arguments={"skill_id": "skill-command-safety"},
        identity=_guest(),
        execution_id="exec-skill-load",
        caller_agent_execution_id="exec-skill-load",
    )
    assert result.output["skill_id"] == "skill-command-safety"
    assert "Before proposing or running a command" in result.output["instruction"]
    assert container.support_loader.is_loaded("skill-command-safety")
    assert not container.support_loader.is_loaded("skill-web-research")
    assert "instruction" not in catalog.get_definition("skill-command-safety").metadata

    with pytest.raises(CapabilityError) as denied:
        await runtime.execute_capability(
            capability_id="skill.load",
            arguments={"skill_id": "skill-web-research"},
            identity=_guest(),
        )
    assert denied.value.code == "CAPABILITY_UNAUTHORIZED"
    assert not container.support_loader.is_loaded("skill-web-research")

    with pytest.raises(CapabilityError) as unknown:
        await runtime.execute_capability(
            capability_id="skill.load",
            arguments={"skill_id": "missing-skill"},
            identity=_guest(),
            execution_id="exec-skill-load",
            caller_agent_execution_id="exec-skill-load",
        )
    assert unknown.value.code == "CAPABILITY_INVALID_ARGUMENT"

    protected = catalog.get_definition("skill-web-research").model_copy(
        update={"required_scopes": ["skill:private"]}
    )
    catalog.register_definition(protected, allow_update=True)
    available = await RegistryAgentSkillResolver(
        agent_registry=container.agent_registry,
        capability_catalog=catalog,
    ).list_available(identity=_guest())
    assert "skill-web-research" not in {item.skill_id for item in available}
    with pytest.raises(CapabilityError) as forbidden:
        await runtime.execute_capability(
            capability_id="skill.load",
            arguments={"skill_id": "skill-web-research"},
            identity=_guest(),
            execution_id="exec-skill-load",
            caller_agent_execution_id="exec-skill-load",
        )
    assert forbidden.value.code == "CAPABILITY_UNAUTHORIZED"
    assert not container.support_loader.is_loaded("skill-web-research")

