from pathlib import Path

from se.src.application.policy.authorization import AuthorizationService
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.tool.registry import ToolRegistry


T9_B_CAPABILITIES = (
    "file.read",
    "file.search",
    "file.write",
    "file.append",
    "file.replace",
    "glob.find",
    "terminal.run",
    "terminal.launch",
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

    assert result["desktop_automation"] == "registered"
    assert result["window_tool"] == "registered"
    for capability_id in T9_B_CAPABILITIES:
        assert result[capability_id] == "registered"
    assert result["web.search"] == "registered"
    assert result["web.read"] == "registered"
    assert result["web.read_many"] == "registered"

    for physical_root in ("file_tool", "find_by_glob", "terminal_tool", "web_tool"):
        assert physical_root not in result
        assert runtime.registry.get_driver(physical_root) is None
        assert tools.get(physical_root) is None

    for capability_id in (*T9_B_CAPABILITIES, "web.search", "web.read", "web.read_many"):
        assert runtime.registry.get_driver(capability_id) is not None
        assert tools.get(capability_id) is not None
        implementation = runtime.catalog.get_implementation(
            f"server:{capability_id}"
        )
        assert implementation.state.value == "ENABLED"
        assert runtime.driver_registry.get(
            implementation.implementation_id
        ) is runtime.registry.get_driver(capability_id)

    assert runtime.registry.get_driver("desktop_automation") is not None
    assert runtime.registry.get_driver("window_tool") is not None
