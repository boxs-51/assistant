from pathlib import Path

from src.application.policy.authorization import AuthorizationService
from src.runtimes.capability.catalog import CapabilityCatalog
from src.runtimes.capability.local_tool_loader import register_local_tools
from src.runtimes.capability.registry import CapabilityRegistry
from src.runtimes.capability.runtime import CapabilityRuntime
from src.tool.registry import ToolRegistry


def test_tools_v1_are_discovered_and_executable_metadata_is_registered():
    runtime = CapabilityRuntime(
        registry=CapabilityRegistry(), authorization=AuthorizationService(), catalog=CapabilityCatalog()
    )
    tools = ToolRegistry(runtime.registry)
    root = Path(__file__).resolve().parents[3] / "tools" / "v1"
    result = register_local_tools(runtime, tools, root)
    assert result["desktop_automation"] == "registered"
    assert runtime.registry.get_driver("desktop_automation") is not None
    assert tools.get("desktop_automation") is not None
    assert runtime.catalog.get_implementation("server:desktop_automation").state.value == "ENABLED"
