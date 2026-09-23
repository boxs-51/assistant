from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from se.src.agent.registry import AgentRegistry
from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
from se.src.runtimes.capability.builtins import register_builtin_support
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.local_tool_loader import register_local_tools
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
    "web.read",
    "web.read_many",
)

ALL_LOGICAL_IDS = (*NON_WEB_IDS, *WEB_IDS)

PHYSICAL_ROOTS = {
    "file_tool",
    "find_by_glob",
    "terminal_tool",
    "window_tool",
    "desktop_automation",
    "web_tool",
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

    assert registry_ids == set(ALL_LOGICAL_IDS)
    assert catalog_ids == set(ALL_LOGICAL_IDS)
    assert model_ids == set(ALL_LOGICAL_IDS)
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


def test_real_support_loader_projects_frozen_agent_tools_only():
    runtime, catalog, _result, container = _runtime_and_container()
    container.agent_registry = AgentRegistry()
    container.agent_runtime = SimpleNamespace()

    discovered = register_builtin_support(container)

    assert discovered["agents"] == [
        "agent-command-reviewer",
        "agent-coordinator",
        "agent-web-researcher",
    ]
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
    ]
    assert web_researcher.tools == list(WEB_IDS)
    assert coordinator.tools == [
        "agent-command-reviewer",
        "agent-web-researcher",
    ]

    loaded_agents = {
        agent.name: agent
        for agent in container.agent_registry.list_all()
    }
    assert set(loaded_agents) == set(discovered["agents"])

    for agent in loaded_agents.values():
        assert not PHYSICAL_ROOTS.intersection(agent.tools)
        assert not {
            capability_id
            for capability_id in NON_WEB_IDS
            if capability_id.startswith(("window.", "desktop."))
        }.intersection(agent.tools)

    for tool_id in (*command_reviewer.tools, *web_researcher.tools):
        assert catalog.contains_definition(tool_id)
        assert catalog.list_implementations(
            tool_id,
            routable_only=True,
        )

    for agent_id in coordinator.tools:
        assert catalog.contains_definition(agent_id)
        definition = catalog.get_definition(agent_id)
        assert definition.kind.value == "AGENT"
        implementations = catalog.list_implementations(
            agent_id,
            routable_only=True,
        )
        assert len(implementations) == 1
        assert implementations[0].implementation_id == (
            f"server:agent:{agent_id}"
        )

    summaries = container.support_loader.list_agent_summaries(_guest())
    summary_by_name = {item.name: item for item in summaries}
    assert set(summary_by_name) == set(discovered["agents"])
    assert summary_by_name["agent-command-reviewer"].tools == (
        command_reviewer.tools
    )
    assert summary_by_name["agent-web-researcher"].tools == (
        web_researcher.tools
    )
    assert summary_by_name["agent-coordinator"].tools == coordinator.tools

