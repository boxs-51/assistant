from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from se.src.application.policy.authorization import AuthorizationService
from se.src.domain.schemas.identity import Identity
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


def test_agent_manifests_keep_frozen_logical_projection_only():
    agents_root = _repo_root() / "agents" / "v1"
    manifest_paths = sorted(agents_root.glob("*/manifest.json"))

    assert [path.parent.name for path in manifest_paths] == [
        "command-reviewer",
        "coordinator",
        "web-researcher",
    ]

    manifests = {
        path.parent.name: json.loads(path.read_text(encoding="utf-8"))
        for path in manifest_paths
    }

    assert manifests["command-reviewer"]["tools"] == [
        "terminal.run",
        "terminal.launch",
        "file.read",
        "file.search",
        "file.write",
        "file.append",
        "file.replace",
        "glob.find",
    ]
    assert manifests["web-researcher"]["tools"] == list(WEB_IDS)
    assert manifests["coordinator"]["tools"] == [
        "agent-command-reviewer",
        "agent-web-researcher",
    ]

    exposed = {
        tool
        for manifest in manifests.values()
        for tool in manifest.get("tools", [])
    }
    assert not PHYSICAL_ROOTS.intersection(exposed)
    assert not {
        capability_id
        for capability_id in NON_WEB_IDS
        if capability_id.startswith(("window.", "desktop."))
    }.intersection(exposed)
