from __future__ import annotations

import inspect
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from cl.src.core.capability_runtime import CapabilityRuntime
from cl.src.core.capability_runtime import CapabilityRuntime
from cl.src.core.local_capability_executor import LocalCapabilityExecutor
from cl.src.loader.local_tools import LocalToolManager


def _manager(root: Path) -> LocalToolManager:
    return LocalToolManager(root, set())


def _write_v1(root: Path, name: str = "legacy.echo") -> None:
    (root / "legacy_tool.py").write_text(
        f"""
TOOL_METADATA = {{
    "name": {name!r},
    "description": "legacy",
    "base_risk": "LOW",
    "parameters": {{
        "type": "object",
        "properties": {{"value": {{"type": "string"}}}},
        "required": ["value"],
    }},
}}

def run(value):
    return {{"value": value}}
""",
        encoding="utf-8",
    )


def _write_v2_package(
    root: Path,
    package_name: str,
    *,
    capability_id: str = "logical.echo",
    bind_literal: str = '{"action": "echo"}',
    run_signature: str = "action, value, invocation_id=None",
    run_body: str = (
        'return {"action": action, "value": value, '
        '"invocation_id": invocation_id}'
    ),
    relative_helper: bool = False,
    expose_root: bool = False,
) -> Path:
    package = root / package_name
    package.mkdir()
    if relative_helper:
        (package / "helper.py").write_text(
            'PREFIX = "relative"\n',
            encoding="utf-8",
        )
        relative_import = "from .helper import PREFIX\n"
        description = 'f"{PREFIX} package"'
    else:
        relative_import = ""
        description = '"canonical package"'

    (package / "__init__.py").write_text(
        f"""
from tools.v1._shared.contracts import tool_result_schema
{relative_import}
TOOL_METADATA = {{
    "manifest_version": "2.0",
    "name": "{package_name}",
    "version": "9.0.0",
    "description": {description},
    "expose_root": {expose_root!r},
    "exports": [
        {{
            "id": "{capability_id}",
            "version": "3.0",
            "name": "{capability_id}",
            "description": "logical export",
            "bind": {bind_literal},
            "input_schema": {{
                "type": "object",
                "properties": {{"value": {{"type": "string"}}}},
                "required": ["value"],
                "additionalProperties": False,
            }},
            "output_schema": tool_result_schema({{}}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["READ"],
            "base_risk": "LOW",
            "required_scopes": ["scope.b", "scope.a"],
            "required_permissions": ["perm.b", "perm.a"],
            "danger_patterns": ["danger.b", "danger.a"],
        }}
    ],
}}

def run({run_signature}):
    {run_body}
""",
        encoding="utf-8",
    )
    return package


def test_v1_top_level_tool_loading_remains_compatible(tmp_path):
    _write_v1(tmp_path)

    loaded = _manager(tmp_path).load_tools(
        {
            "allowed_local_tools": ["*"],
            "blocked_local_tools": [],
        }
    )

    assert set(loaded) == {"legacy.echo"}
    assert loaded["legacy.echo"]["func"]("x") == {"value": "x"}




def test_loader_does_not_reuse_stale_module_from_another_tools_root(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()

    _write_v1(first_root, "legacy.first")
    _write_v1(second_root, "legacy.second")

    first = _manager(first_root).load_tools(
        {"allowed_local_tools": ["*"]}
    )
    second = _manager(second_root).load_tools(
        {"allowed_local_tools": ["*"]}
    )

    assert set(first) == {"legacy.first"}
    assert set(second) == {"legacy.second"}
    assert (
        second["legacy.second"]["file_path"]
        == str(second_root / "legacy_tool.py")
    )


def test_local_tools_package_path_prefers_current_tools_root(tmp_path):
    first_root = tmp_path / "first-rel"
    second_root = tmp_path / "second-rel"
    first_root.mkdir()
    second_root.mkdir()

    (first_root / "helper.py").write_text(
        "VALUE = 'first'\n",
        encoding="utf-8",
    )
    (second_root / "helper.py").write_text(
        "VALUE = 'second'\n",
        encoding="utf-8",
    )
    for root in (first_root, second_root):
        (root / "relative_tool.py").write_text(
            """
from .helper import VALUE
TOOL_METADATA = {
    "name": f"legacy.{VALUE}",
    "description": "relative",
    "parameters": {"type": "object"},
}
def run():
    return VALUE
""",
            encoding="utf-8",
        )

    first = _manager(first_root).load_tools(
        {"allowed_local_tools": ["*"]}
    )
    second = _manager(second_root).load_tools(
        {"allowed_local_tools": ["*"]}
    )

    assert set(first) == {"legacy.first"}
    assert set(second) == {"legacy.second"}
    assert second["legacy.second"]["func"]() == "second"


def test_v2_package_is_discovered_but_not_selected_by_default(tmp_path):
    package = _write_v2_package(tmp_path, "pkg_default")

    loaded = _manager(tmp_path).load_tools(
        {
            "allowed_local_tools": ["*"],
            "blocked_local_tools": [],
        }
    )

    assert loaded == {}
    assert package.joinpath("__init__.py").is_file()


def test_exact_enabled_v2_capability_creates_normalized_logical_entry(tmp_path):
    _write_v2_package(
        tmp_path,
        "pkg_enabled",
        capability_id="logical.enabled",
    )

    loaded = _manager(tmp_path).load_tools(
        {
            "allowed_local_tools": ["*"],
            "enabled_v2_capabilities": ["logical.enabled"],
        }
    )

    assert set(loaded) == {"logical.enabled"}
    entry = loaded["logical.enabled"]
    metadata = entry["metadata"]
    assert metadata["name"] == "logical.enabled"
    assert metadata["version"] == "3.0"
    assert metadata["physical_tool"] == "pkg_enabled"
    assert metadata["physical_version"] == "9.0.0"
    assert metadata["manifest_version"] == "2.0"
    assert metadata["bind"] == {"action": "echo"}
    assert metadata["effects"] == ["READ"]
    assert metadata["required_scopes"] == ["scope.a", "scope.b"]
    assert metadata["required_permissions"] == ["perm.a", "perm.b"]
    assert metadata["danger_patterns"] == ["danger.a", "danger.b"]


def test_v2_wildcard_selection_is_rejected(tmp_path):
    _write_v2_package(tmp_path, "pkg_wildcard")

    with pytest.raises(ValueError, match="does not support '\\*'"):
        _manager(tmp_path).load_tools(
            {
                "allowed_local_tools": ["*"],
                "enabled_v2_capabilities": ["*"],
            }
        )


def test_package_relative_imports_work(tmp_path):
    _write_v2_package(
        tmp_path,
        "pkg_relative",
        capability_id="logical.relative",
        relative_helper=True,
    )

    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": ["logical.relative"],
        }
    )

    assert set(loaded) == {"logical.relative"}


def test_helper_package_without_tool_metadata_is_ignored(tmp_path):
    package = tmp_path / "helper_pkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        "VALUE = 42\n",
        encoding="utf-8",
    )

    loaded = _manager(tmp_path).load_tools({})

    assert loaded == {}


def test_reserved_runtime_context_bind_key_is_rejected(tmp_path):
    _write_v2_package(
        tmp_path,
        "pkg_reserved",
        capability_id="logical.reserved",
        bind_literal='{"action": "echo", "invocation_id": "spoofed"}',
    )

    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": ["logical.reserved"],
        }
    )

    assert loaded == {}


def test_bound_wrapper_preserves_effective_physical_signature_and_context_injection(
    tmp_path,
):
    _write_v2_package(
        tmp_path,
        "pkg_signature",
        capability_id="logical.signature",
        bind_literal='{"action": "echo"}',
        run_signature="action, value, invocation_id=None",
        run_body=(
            'return {"action": action, "value": value, '
            '"invocation_id": invocation_id}'
        ),
    )
    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": ["logical.signature"],
        }
    )
    target = loaded["logical.signature"]["func"]

    parameters = inspect.signature(target).parameters
    assert "action" not in parameters
    assert list(parameters) == ["value", "invocation_id"]
    assert all(
        parameter.kind != inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )

    result = LocalCapabilityExecutor._call(
        target,
        {"value": "x"},
        {
            "invocation_id": "inv-1",
            "connection_id": "conn-1",
            "session_id": "sess-1",
        },
        threading.Event(),
    )
    assert result == {
        "action": "echo",
        "value": "x",
        "invocation_id": "inv-1",
    }


def test_nested_bind_is_deeply_immutable_across_client_calls(tmp_path):
    _write_v2_package(
        tmp_path,
        "pkg_nested",
        capability_id="logical.nested",
        bind_literal=(
            '{"action": "nested", '
            '"options": {"tags": ["original"]}}'
        ),
        run_signature="action, options, value",
        run_body=(
            'options["tags"].append("mutated"); '
            'return {"action": action, "tags": list(options["tags"]), '
            '"value": value}'
        ),
    )
    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": ["logical.nested"],
        }
    )
    entry = loaded["logical.nested"]
    target = entry["func"]

    first = target(value="first")
    second = target(value="second")

    assert first["tags"] == ["original", "mutated"]
    assert second["tags"] == ["original", "mutated"]
    assert entry["metadata"]["bind"] == {
        "action": "nested",
        "options": {"tags": ["original"]},
    }


def test_expose_root_true_v2_package_is_not_client_executable(tmp_path):
    _write_v2_package(
        tmp_path,
        "pkg_root",
        capability_id="logical.root",
        expose_root=True,
    )

    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": ["logical.root"],
        }
    )

    assert loaded == {}


def test_failed_package_import_cleans_relative_submodules(tmp_path):
    package = tmp_path / "pkg_failed"
    package.mkdir()
    (package / "helper.py").write_text(
        "VALUE = 42\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        """
from .helper import VALUE
raise RuntimeError("boom")
""",
        encoding="utf-8",
    )

    manager = _manager(tmp_path)
    loaded = manager.load_tools({})

    assert loaded == {}
    assert "local_tools.pkg_failed" not in __import__("sys").modules
    assert "local_tools.pkg_failed.helper" not in __import__("sys").modules


class _RegistrationRealtime:
    def __init__(self, events=None):
        self.connection_id = "conn-v2"
        self.is_registered = True
        self.sent = []
        self.events = [] if events is None else events

    def send(self, message_type, payload):
        self.sent.append((message_type, payload))
        self.events.append(("send", message_type, payload))

    def wait_capabilities_registered(self, timeout):
        self.events.append(("wait", timeout))
        return {
            "type": "capability.registered",
            "payload": {
                "capabilities": [
                    {
                        "capability_id": "logical.registered",
                        "state": "ENABLED",
                    }
                ]
            },
        }


class _RegistrationDispatcher:
    def __init__(self, events=None):
        self.snapshots = []
        self.events = [] if events is None else events

    def update_registration_snapshot(self, capability_ids):
        snapshot = tuple(capability_ids)
        self.snapshots.append(snapshot)
        self.events.append(("snapshot", snapshot))

    def shutdown(self):
        return None


def _client_capability_runtime(loaded_tools, events=None):
    events = [] if events is None else events
    realtime = _RegistrationRealtime(events)
    dispatcher = _RegistrationDispatcher(events)
    runtime = CapabilityRuntime(
        SimpleNamespace(tools=loaded_tools),
        realtime,
        client_id="client-v2",
        owner_id="user-v2",
        dispatcher=dispatcher,
    )
    return runtime, realtime, dispatcher


def test_repository_default_config_uses_explicit_v2_ids_without_web():
    config_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "setting.json"
    )
    settings = json.loads(config_path.read_text(encoding="utf-8"))

    enabled = settings["tools_config"]["enabled_v2_capabilities"]
    assert isinstance(enabled, list)
    assert "*" not in enabled
    assert all(
        isinstance(capability_id, str) and capability_id
        for capability_id in enabled
    )
    assert not any(
        capability_id.startswith("web.")
        for capability_id in enabled
    )


def test_v2_registration_separates_logical_definition_from_provenance(tmp_path):
    _write_v2_package(
        tmp_path,
        "pkg_registration",
        capability_id="logical.registered",
    )
    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": ["logical.registered"],
        }
    )
    runtime, _, _ = _client_capability_runtime(loaded)

    payload = runtime.build_registration()

    assert payload["connection_id"] == "conn-v2"
    assert payload["client_id"] == "client-v2"
    assert payload["owner_id"] == "user-v2"
    assert len(payload["capabilities"]) == 1

    registration = payload["capabilities"][0]
    definition = registration["definition"]

    assert definition["id"] == "logical.registered"
    assert definition["version"] == "3.0"
    assert definition["name"] == "logical.registered"
    assert definition["source"] == "LOCAL"
    assert definition["execution_kind"] == "PYTHON"
    assert definition["kind"] == "TOOL"
    assert definition["execution_mode"] == "ONE_SHOT"
    assert definition["idempotency"] == "UNKNOWN"
    assert definition["effects"] == ["READ"]
    assert definition["require_auth"] is False
    assert definition["required_scopes"] == ["scope.a", "scope.b"]
    assert definition["parameters"] == loaded[
        "logical.registered"
    ]["metadata"]["input_schema"]
    assert definition["output_schema"] == loaded[
        "logical.registered"
    ]["metadata"]["output_schema"]
    assert definition["metadata"] == {
        "base_risk": "LOW",
        "required_permissions": ["perm.a", "perm.b"],
        "danger_patterns": ["danger.a", "danger.b"],
    }
    assert "client_id" not in definition["metadata"]
    assert "physical_tool" not in definition["metadata"]
    assert "bind" not in definition["metadata"]

    assert registration["location"] == "CLIENT"
    assert registration["driver_kind"] == "REMOTE_CLIENT"
    assert registration["owner_type"] == "CLIENT"
    assert registration["owner_id"] == "user-v2"
    assert registration["connection_id"] == "conn-v2"
    assert registration["implementation_id"] == (
        "conn-v2:logical.registered"
    )
    assert registration["metadata"] == {
        "client_id": "client-v2",
        "local_name": "logical.registered",
        "physical_tool": "pkg_registration",
        "physical_version": "9.0.0",
        "bind": {"action": "echo"},
        "manifest_version": "2.0",
    }
    assert definition["version"] != registration["metadata"][
        "physical_version"
    ]

    registration["metadata"]["bind"]["action"] = "mutated"
    assert loaded["logical.registered"]["metadata"]["bind"] == {
        "action": "echo"
    }


def test_v1_registration_payload_remains_compatible(tmp_path):
    _write_v1(tmp_path, "legacy.registration")
    loaded = _manager(tmp_path).load_tools(
        {
            "allowed_local_tools": ["*"],
        }
    )
    runtime, _, _ = _client_capability_runtime(loaded)

    registration = runtime.build_registration()["capabilities"][0]

    assert registration["definition"]["id"] == "legacy.registration"
    assert registration["definition"]["source"] == "CLIENT"
    assert registration["definition"]["execution_kind"] == "PYTHON"
    assert registration["definition"]["metadata"] == {
        "client_id": "client-v2",
    }
    assert registration["metadata"] == {
        "client_id": "client-v2",
        "local_name": "legacy.registration",
    }


def test_v2_register_preserves_snapshot_send_and_ack_flow(tmp_path):
    _write_v2_package(
        tmp_path,
        "pkg_register_flow",
        capability_id="logical.registered",
    )
    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": ["logical.registered"],
        }
    )
    events = []
    runtime, realtime, dispatcher = _client_capability_runtime(
        loaded,
        events,
    )

    ack = runtime.register(timeout=7.5)

    assert dispatcher.snapshots == [("logical.registered",)]
    assert len(realtime.sent) == 1
    message_type, payload = realtime.sent[0]
    assert message_type == "capability.register"
    assert payload["capabilities"][0]["definition"]["id"] == (
        "logical.registered"
    )
    assert [event[0] for event in events] == [
        "snapshot",
        "send",
        "wait",
    ]
    assert events[-1] == ("wait", 7.5)
    assert ack["type"] == "capability.registered"



def _write_v1_collision(root: Path, filename: str) -> None:
    (root / filename).write_text(
        """
TOOL_METADATA = {
    "name": "logical.collision",
    "description": "legacy collision",
    "base_risk": "LOW",
    "parameters": {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    },
}
def run(value):
    return {"legacy": value}
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("package_name", "v1_filename"),
    [
        ("aaa_v2", "zzz_legacy.py"),
        ("zzz_v2", "aaa_legacy.py"),
    ],
)
def test_v1_v2_identity_collision_is_fail_closed_regardless_of_order(
    tmp_path,
    package_name,
    v1_filename,
):
    package = _write_v2_package(
        tmp_path,
        package_name,
        capability_id="logical.collision",
    )
    assert package.joinpath("__init__.py").is_file()
    _write_v1_collision(tmp_path, v1_filename)

    loaded = _manager(tmp_path).load_tools(
        {
            "allowed_local_tools": ["*"],
            "enabled_v2_capabilities": ["logical.collision"],
        }
    )

    assert "logical.collision" not in loaded


def _write_multi_export_v2_package(
    root: Path,
    package_name: str,
) -> None:
    package = root / package_name
    package.mkdir()
    (package / "__init__.py").write_text(
        """
from tools.v1._shared.contracts import tool_result_schema

TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": "multi_pkg",
    "version": "1.0.0",
    "description": "multi export collision fixture",
    "expose_root": False,
    "exports": [
        {
            "id": "logical.collision",
            "version": "1.0",
            "name": "logical.collision",
            "description": "colliding export",
            "bind": {"action": "collision"},
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["READ"],
            "base_risk": "LOW",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "logical.survivor",
            "version": "1.0",
            "name": "logical.survivor",
            "description": "non-colliding sibling",
            "bind": {"action": "survivor"},
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["READ"],
            "base_risk": "LOW",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
    ],
}

def run(action):
    return {"action": action}
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("package_name", "v1_filename"),
    [
        ("aaa_multi_v2", "zzz_legacy.py"),
        ("zzz_multi_v2", "aaa_legacy.py"),
    ],
)
def test_multi_export_v1_v2_collision_disables_same_package_in_both_orders(
    tmp_path,
    package_name,
    v1_filename,
):
    _write_multi_export_v2_package(tmp_path, package_name)
    _write_v1_collision(tmp_path, v1_filename)

    loaded = _manager(tmp_path).load_tools(
        {
            "allowed_local_tools": ["*"],
            "enabled_v2_capabilities": [
                "logical.collision",
                "logical.survivor",
            ],
        }
    )

    assert "logical.collision" not in loaded
    assert "logical.survivor" not in loaded


def _write_multi_export_v2_variant(
    root: Path,
    package_name: str,
    sibling_id: str,
) -> None:
    _write_multi_export_v2_package(root, package_name)
    init_path = root / package_name / "__init__.py"
    source = init_path.read_text(encoding="utf-8")
    source = source.replace(
        '"name": "multi_pkg"',
        f'"name": "{package_name}"',
        1,
    )
    source = source.replace(
        "logical.survivor",
        sibling_id,
    )
    init_path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("first_name", "second_name"),
    [
        ("aaa_v2_one", "zzz_v2_two"),
        ("zzz_v2_one", "aaa_v2_two"),
    ],
)
def test_multi_export_v2_v2_collision_is_package_fail_closed_in_both_orders(
    tmp_path,
    first_name,
    second_name,
):
    _write_multi_export_v2_variant(
        tmp_path,
        first_name,
        "logical.first_only",
    )
    _write_multi_export_v2_variant(
        tmp_path,
        second_name,
        "logical.second_only",
    )

    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": [
                "logical.collision",
                "logical.first_only",
                "logical.second_only",
            ],
        }
    )

    assert loaded == {}


def _write_multi_export_v2_pair(
    root: Path,
    package_name: str,
    first_id: str,
    second_id: str,
) -> None:
    _write_multi_export_v2_variant(
        root,
        package_name,
        second_id,
    )
    init_path = root / package_name / "__init__.py"
    source = init_path.read_text(encoding="utf-8")
    source = source.replace(
        "logical.collision",
        first_id,
    )
    init_path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("p1_name", "p2_name", "p3_name"),
    [
        ("aaa_p1", "bbb_p2", "ccc_p3"),
        ("ccc_p1", "bbb_p2", "aaa_p3"),
    ],
)
def test_transitive_v2_collision_components_cannot_resurrect_ids(
    tmp_path,
    p1_name,
    p2_name,
    p3_name,
):
    # Collision graph:
    # P1={A,B}, P2={A,C}, P3={B,D}. Once P1/P2 collide,
    # B must remain tombstoned so a later P3 cannot resurrect it.
    _write_multi_export_v2_pair(
        tmp_path,
        p1_name,
        "logical.a",
        "logical.b",
    )
    _write_multi_export_v2_pair(
        tmp_path,
        p2_name,
        "logical.a",
        "logical.c",
    )
    _write_multi_export_v2_pair(
        tmp_path,
        p3_name,
        "logical.b",
        "logical.d",
    )

    loaded = _manager(tmp_path).load_tools(
        {
            "enabled_v2_capabilities": [
                "logical.a",
                "logical.b",
                "logical.c",
                "logical.d",
            ],
        }
    )

    assert loaded == {}


def test_real_web_package_is_discoverable_but_not_selected_by_default():
    tools_root = Path(__file__).resolve().parents[2] / "tools" / "v1"
    manager = LocalToolManager(tools_root, set())

    candidates = manager._entrypoint_candidates()
    web_entry = tools_root / "web_tool" / "__init__.py"
    assert (web_entry, True) in candidates

    module = manager._load_entrypoint(web_entry, is_package=True)
    assert module is not None
    assert module.TOOL_METADATA["manifest_version"] == "2.0"
    assert {
        export["id"]
        for export in module.TOOL_METADATA["exports"]
    } == {"web.search", "web.read", "web.read_many"}

    entries = manager._canonical_v2_entries(
        metadata=module.TOOL_METADATA,
        handler=module.run,
        enabled=frozenset(),
        fpath=web_entry,
    )
    assert entries == {}
