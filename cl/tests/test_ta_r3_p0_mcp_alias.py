"""TA-V1-R3 P0-MCP: canonical alias resolution and no-dispatch negatives.

No MCP process starts: the registry is populated with synthetic handlers.
"""
from __future__ import annotations

import pytest

from cl.src.loader.registry import DynamicRegistry
from cl.src.core.local_capability_executor import LocalCapabilityExecutor


def _registry(order):
    registry = DynamicRegistry()
    registry.tools = {
        name: {"func": lambda **kwargs: "UNEXPECTED_EFFECT", "metadata": {}}
        for name in order
    }
    return registry


@pytest.mark.parametrize("order", [
    ("mcp__alpha__run", "mcp__beta__run"),
    ("mcp__beta__run", "mcp__alpha__run"),
])
def test_ambiguous_mcp_alias_is_denied_in_every_load_order(order):
    registry = _registry(order)
    with pytest.raises(ValueError, match="AMBIGUOUS_MCP_ALIAS"):
        registry.resolve_tool("run")
    assert registry.get_tool("run") is None
    executor = LocalCapabilityExecutor(registry)
    target, metadata = executor._resolve("run")
    assert target is None and metadata == {}


def test_exact_canonical_tool_is_stable_even_with_suffix_collision():
    registry = _registry(("mcp__alpha__run", "mcp__beta__run"))
    canonical, entry = registry.resolve_tool("mcp__alpha__run")
    assert canonical == "mcp__alpha__run"
    assert entry is registry.tools[canonical]
    assert registry.get_tool(canonical) is entry


def test_unique_short_alias_resolves_to_exact_canonical_entry():
    registry = _registry(("mcp__alpha__run", "mcp__beta__read"))
    canonical, entry = registry.resolve_tool("run")
    assert canonical == "mcp__alpha__run"
    assert entry is registry.tools[canonical]


def test_unknown_alias_has_no_binding():
    registry = _registry(("mcp__alpha__read",))
    assert registry.resolve_tool("run") is None
    assert registry.get_tool("run") is None


def test_mcp_handler_pins_discovered_session_without_starting_a_process():
    from types import SimpleNamespace
    from cl.src.mcp_client.mcp_adapter import MCPClientAdapter

    calls = []
    adapter = MCPClientAdapter.__new__(MCPClientAdapter)
    adapter.client = object()
    adapter.session = object()
    adapter._lifecycle_task = object()
    adapter._startup_phase = "READY"
    adapter._manager = SimpleNamespace(
        invoke=lambda self_adapter, tool, args, cancel_event=None: (
            calls.append((self_adapter, tool, args)) or "safe"
        )
    )
    handler = adapter._create_execution_handler("innocent_preview")
    assert handler(value="x") == "safe"
    assert calls == [(adapter, "innocent_preview", {"value": "x"})]

    # Wrapper code and tool name stay identical, but physical session changes.
    adapter.client = object()
    with pytest.raises(RuntimeError, match="MCP_PHYSICAL_HANDLER_BINDING_CHANGED"):
        handler(value="y")
    assert len(calls) == 1


def test_mcp_handler_denies_lifecycle_shutdown_before_call():
    from types import SimpleNamespace
    from cl.src.mcp_client.mcp_adapter import MCPClientAdapter

    adapter = MCPClientAdapter.__new__(MCPClientAdapter)
    adapter.client = object()
    adapter.session = object()
    adapter._lifecycle_task = object()
    adapter._startup_phase = "READY"
    adapter._manager = SimpleNamespace(
        invoke=lambda *a, **k: pytest.fail("physical MCP invocation escaped pin")
    )
    handler = adapter._create_execution_handler("innocent_preview")
    adapter._startup_phase = "CLOSED"
    with pytest.raises(RuntimeError, match="MCP_PHYSICAL_HANDLER_BINDING_CHANGED"):
        handler(value="x")
