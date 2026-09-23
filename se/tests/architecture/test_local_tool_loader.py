from pathlib import Path

from se.src.application.policy.authorization import AuthorizationService
from se.src.runtimes.capability.catalog import CapabilityCatalog
from se.src.runtimes.capability.local_tool_loader import register_local_tools
from se.src.runtimes.capability.registry import CapabilityRegistry
from se.src.runtimes.capability.runtime import CapabilityRuntime
from se.src.tool.registry import ToolRegistry


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
    assert result["web.search"] == "registered"
    assert result["web.read"] == "registered"
    assert result["web.read_many"] == "registered"
    assert "web_tool" not in result

    assert runtime.registry.get_driver("desktop_automation") is not None
    assert runtime.registry.get_driver("web.search") is not None
    assert runtime.registry.get_driver("web.read") is not None
    assert runtime.registry.get_driver("web.read_many") is not None
    assert runtime.registry.get_driver("web_tool") is None

    assert tools.get("desktop_automation") is not None
    assert tools.get("web.search") is not None
    assert tools.get("web.read") is not None
    assert tools.get("web.read_many") is not None
    assert tools.get("web_tool") is None

    assert (
        runtime.catalog.get_implementation(
            "server:desktop_automation"
        ).state.value
        == "ENABLED"
    )
    for capability_id in ("web.search", "web.read", "web.read_many"):
        implementation = runtime.catalog.get_implementation(
            f"server:{capability_id}"
        )
        assert implementation.state.value == "ENABLED"
        assert runtime.driver_registry.get(
            implementation.implementation_id
        ) is runtime.registry.get_driver(capability_id)
