from pathlib import Path

from se.src.application.policy.authorization import AuthorizationService
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.tool.registry import ToolRegistry


T9_CAPABILITIES = (
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


def test_tools_v1_are_discovered_and_executable_metadata_is_registered():
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(),
        authorization=AuthorizationService(),
        catalog=CapabilityCatalog(),
    )
    tools = ToolRegistry(runtime.registry)
    root = Path(__file__).resolve().parents[3] / "tools" / "v1"

    result = register_local_tools(runtime, tools, root)

    for capability_id in T9_CAPABILITIES:
        assert result[capability_id] == "registered"
    assert result["web.search"] == "registered"
    assert result["web.read"] == "registered"
    assert result["web.read_many"] == "registered"

    physical_roots = (
        "file_tool",
        "find_by_glob",
        "terminal_tool",
        "window_tool",
        "desktop_automation",
        "web_tool",
    )
    for physical_root in physical_roots:
        assert physical_root not in result
        assert runtime.registry.get_driver(physical_root) is None
        assert tools.get(physical_root) is None

    for capability_id in (*T9_CAPABILITIES, "web.search", "web.read", "web.read_many"):
        assert runtime.registry.get_driver(capability_id) is not None
        assert tools.get(capability_id) is not None
        implementation = runtime.catalog.get_implementation(
            f"server:{capability_id}"
        )
        assert implementation.state.value == "ENABLED"
        assert runtime.driver_registry.get(
            implementation.implementation_id
        ) is runtime.registry.get_driver(capability_id)
