from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cl.src.core.capability_runtime import CapabilityRuntime as ClientCapabilityRuntime
from cl.src.loader.local_tools import LocalToolManager
from se.src.application.policy.authorization import AuthorizationService
from se.src.runtimes.capability.catalog import (
    CapabilityCatalog,
    CapabilityDefinitionConflictError,
)
from se.src.runtimes.capability.contracts.definition import CapabilityDefinition
from se.src.runtimes.capability.contracts.implementation import (
    CapabilityExecutionLocation,
)
from se.src.runtimes.capability.contracts.registration import (
    ClientCapabilityRegistration,
)
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.policy import (
    CapabilityRequestContext,
    CapabilityRoutingPolicy,
)
from se.src.runtimes.capability.registration import (
    ClientCapabilityRegistrationService,
)
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.runtimes.connection.registry import ConnectionRegistry
from se.src.tool.registry import ToolRegistry


T9_IDS = (
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

PHYSICAL_TOOL_BY_ID = {
    **{capability_id: "file_tool" for capability_id in T9_IDS[:5]},
    "glob.find": "find_by_glob",
    "terminal.run": "terminal_tool",
    "terminal.launch": "terminal_tool",
    **{capability_id: "window_tool" for capability_id in T9_IDS[8:16]},
    **{
        capability_id: "desktop_automation"
        for capability_id in T9_IDS[16:]
    },
}

CONNECTION_ID = "conn-t9-d"
CLIENT_ID = "desktop-t9-d"
OWNER_ID = "user-t9-d"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _tools_root() -> Path:
    return _repo_root() / "tools" / "v1"


def _client_config() -> dict:
    path = _repo_root() / "cl" / "config" / "setting.json"
    return json.loads(path.read_text(encoding="utf-8"))["tools_config"]


def _server_catalog() -> CapabilityCatalog:
    catalog = CapabilityCatalog()
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=catalog,
    )
    tools = ToolRegistry(runtime.registry)

    registered = register_local_tools(runtime, tools, _tools_root())
    assert all(registered[capability_id] == "registered" for capability_id in T9_IDS)
    return catalog


def _client_request() -> ClientCapabilityRegistration:
    loaded = LocalToolManager(_tools_root(), set()).load_tools(
        _client_config()
    )
    assert set(loaded) == set(T9_IDS)

    realtime = SimpleNamespace(connection_id=CONNECTION_ID)
    runtime = ClientCapabilityRuntime(
        SimpleNamespace(tools=loaded),
        realtime,
        client_id=CLIENT_ID,
        owner_id=OWNER_ID,
        dispatcher=SimpleNamespace(),
    )
    payload = runtime.build_registration()
    return ClientCapabilityRegistration.model_validate(payload)


def _active_connections() -> ConnectionRegistry:
    connections = ConnectionRegistry()
    connections.register(
        "sess-t9-d",
        OWNER_ID,
        connection_id=CONNECTION_ID,
    )
    connections.activate(CONNECTION_ID)
    return connections


def _definition_dump(definition: CapabilityDefinition) -> dict:
    value = definition.model_dump(mode="json")
    value["effects"] = sorted(value["effects"])
    value["required_scopes"] = sorted(value["required_scopes"])
    metadata = dict(value["metadata"])
    metadata["required_permissions"] = sorted(
        metadata.get("required_permissions", [])
    )
    metadata["danger_patterns"] = sorted(
        metadata.get("danger_patterns", [])
    )
    value["metadata"] = metadata
    return value


def test_real_server_and_client_project_identical_24_logical_definitions():
    catalog = _server_catalog()
    request = _client_request()

    client_definitions = {
        item.definition.capability_id: item.definition
        for item in request.capabilities
    }
    assert tuple(client_definitions) == T9_IDS

    for capability_id in T9_IDS:
        server_definition = catalog.get_definition(capability_id)
        client_definition = client_definitions[capability_id]

        assert _definition_dump(client_definition) == _definition_dump(
            server_definition
        )
        assert server_definition.version == "1.0"
        assert client_definition.version == "1.0"

        registration = next(
            item
            for item in request.capabilities
            if item.definition.capability_id == capability_id
        )
        assert registration.metadata["physical_tool"] == (
            PHYSICAL_TOOL_BY_ID[capability_id]
        )
        assert registration.metadata["physical_version"] == "2.0.0"
        assert registration.metadata["manifest_version"] == "2.0"
        assert registration.metadata["physical_version"] != (
            client_definition.version
        )

        server_implementation = catalog.get_implementation(
            f"server:{capability_id}"
        )
        assert server_implementation.version == "1.0"
        assert server_implementation.metadata["physical_tool"] == (
            PHYSICAL_TOOL_BY_ID[capability_id]
        )
        assert server_implementation.metadata["physical_version"] == "2.0.0"
        assert server_implementation.metadata["physical_version"] != (
            server_implementation.version
        )


def test_real_24_client_implementations_coexist_without_definition_rewrite():
    catalog = _server_catalog()
    before = {
        capability_id: _definition_dump(
            catalog.get_definition(capability_id)
        )
        for capability_id in T9_IDS
    }
    connections = _active_connections()
    service = ClientCapabilityRegistrationService(catalog, connections)

    registered = service.register(_client_request())

    assert len(registered) == 24
    assert {
        item.capability_id for item in registered
    } == set(T9_IDS)
    assert all(
        item.location is CapabilityExecutionLocation.CLIENT
        for item in registered
    )

    for capability_id in T9_IDS:
        assert _definition_dump(
            catalog.get_definition(capability_id)
        ) == before[capability_id]
        implementations = catalog.list_implementations(capability_id)
        assert {
            item.implementation_id for item in implementations
        } == {
            f"server:{capability_id}",
            f"{CONNECTION_ID}:{capability_id}",
        }
        assert {
            item.location for item in implementations
        } == {
            CapabilityExecutionLocation.SERVER,
            CapabilityExecutionLocation.CLIENT,
        }


def test_divergent_real_24_batch_rejects_atomically_without_client_mutation():
    catalog = _server_catalog()
    before = {
        capability_id: _definition_dump(
            catalog.get_definition(capability_id)
        )
        for capability_id in T9_IDS
    }
    request = _client_request().model_copy(deep=True)

    divergent_id = "window.geometry"
    divergent = next(
        item
        for item in request.capabilities
        if item.definition.capability_id == divergent_id
    )
    divergent.definition.description = (
        divergent.definition.description + " divergent"
    )

    service = ClientCapabilityRegistrationService(
        catalog,
        _active_connections(),
    )
    with pytest.raises(
        CapabilityDefinitionConflictError,
        match="different contract",
    ):
        service.register(request)

    for capability_id in T9_IDS:
        assert _definition_dump(
            catalog.get_definition(capability_id)
        ) == before[capability_id]
        assert not catalog.contains_implementation(
            f"{CONNECTION_ID}:{capability_id}"
        )
        assert {
            item.implementation_id
            for item in catalog.list_implementations(capability_id)
        } == {f"server:{capability_id}"}


def test_existing_routing_priority_prefers_same_connection_client_then_server():
    catalog = _server_catalog()
    connections = _active_connections()
    service = ClientCapabilityRegistrationService(catalog, connections)
    service.register(_client_request())

    policy = CapabilityRoutingPolicy(
        connection_availability=connections,
    )
    same_connection = CapabilityRequestContext(
        owner_id=OWNER_ID,
        connection_id=CONNECTION_ID,
    )

    for capability_id in T9_IDS:
        selected = policy.select(
            catalog,
            capability_id,
            context=same_connection,
        )
        assert selected.implementation_id == (
            f"{CONNECTION_ID}:{capability_id}"
        )

        fallback = policy.select(
            catalog,
            capability_id,
            context=same_connection,
            excluded_implementation_ids=frozenset(
                {f"{CONNECTION_ID}:{capability_id}"}
            ),
        )
        assert fallback.implementation_id == f"server:{capability_id}"
        assert fallback.location is CapabilityExecutionLocation.SERVER
