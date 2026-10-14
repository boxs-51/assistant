"""RED-first: model-origin LOCAL_OFFLINE process tools must never spawn on host.

The test intentionally uses the REAL local Metadata V2 TerminalTool export and
legacy ToolExecutor batch path. Commands only run a benign short-lived Python
sentinel that writes a temporary file if the host dispatch is reached.
An unfixed tree MUST stay RED; never xfail/skip, fake a negative, or weaken
the oracle. Trusted DIRECT is not called or modified.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest.mock import patch

import pytest

from cl.src.core.tool_executor import ExecutionContext, ToolExecutor
from cl.src.loader.local_tools import LocalToolManager
from cl.src.loader.registry import DynamicRegistry
from cl.src.schemas.tool import FunctionCall, GatewayToolCall

ROOT = Path(__file__).resolve().parents[2]
TERMINAL_IDS = ("terminal.run", "terminal.launch")


class _ApprovedTestHITL:
    """Proof must remain fail-closed EVEN AFTER positive legacy HITL."""

    def request_approval(self, *_args, **_kwargs):
        return True


@pytest.fixture(scope="module")
def actual_terminal_exports():
    cfg = json.loads(
        (ROOT / "cl/config/setting.json").read_text(encoding="utf-8")
    )["tools_config"]
    assert set(TERMINAL_IDS).issubset(cfg["enabled_v2_capabilities"])
    tools = LocalToolManager(ROOT / "tools/v1", set()).load_tools(cfg)
    for logical in TERMINAL_IDS:
        assert logical in tools
        assert tools[logical]["metadata"]["physical_tool"] == "terminal_tool"
        assert "EXECUTE" in tools[logical]["metadata"]["effects"]
    return tools


def _sentinel_command(path: Path) -> str:
    script = f"from pathlib import Path; Path({str(path)!r}).write_text('host-spawned')"
    parts = [sys.executable, "-c", script]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


def _registry(actual_terminal_exports, *, suffix_alias: bool, logical: str):
    # Use the production DynamicRegistry.get_tool(), including its
    # '__<logical>' suffix resolution. Avoid starting unrelated MCP services.
    registry = DynamicRegistry.__new__(DynamicRegistry)
    if suffix_alias:
        registry.tools = {"mcp__legacy__" + logical: actual_terminal_exports[logical]}
    else:
        registry.tools = dict(actual_terminal_exports)
    return registry


@pytest.mark.parametrize("logical", TERMINAL_IDS)
@pytest.mark.parametrize("suffix_alias", (False, True), ids=("canonical", "mcp-suffix"))
def test_offline_agent_approved_terminal_never_spawns_host(
    actual_terminal_exports, tmp_path, logical, suffix_alias
):
    registry = _registry(
        actual_terminal_exports, suffix_alias=suffix_alias, logical=logical
    )
    # Both variants are model-supplied canonical names; the second resolves
    # through the production legacy suffix fallback into the same host tool.
    assert registry.get_tool(logical) is not None
    executor = ToolExecutor(registry, _ApprovedTestHITL(), max_workers=2)
    marker = tmp_path / f"forbidden-{logical.replace('.', '-')}.txt"
    call = GatewayToolCall(
        id=f"model-{logical}",
        function=FunctionCall(
            name=logical,
            arguments=json.dumps({"command": _sentinel_command(marker)}),
        ),
    )
    execution = ExecutionContext(
        conversation_id="offline-red-conversation",
        execution_id="offline-red-agent",
        session=object(),
    )
    # wraps=... observes the REAL process spawn, not a mocked-out fake path.
    original_popen = subprocess.Popen
    with patch("subprocess.Popen", wraps=original_popen) as host_popen:
        responses = executor.execute_tools_parallel(
            [call], session=execution.session, execution_context=execution
        )
        count = host_popen.call_count
    assert len(responses) == 1
    assert count == 0, (
        f"RED P0: LOCAL_OFFLINE Agent {logical} (alias={suffix_alias}) "
        f"reached host subprocess.Popen {count} time(s)"
    )
    assert not marker.exists(), "RED P0: model-originated host command executed"
    assert responses[0].is_error is True, "Model-originated process was not denied"
    assert "AGENT_TOOL_POLICY_DENIED" in str(responses[0].content)


def test_offline_agent_mixed_parallel_batch_denies_all_process_spawns(
    actual_terminal_exports, tmp_path
):
    registry = _registry(
        actual_terminal_exports, suffix_alias=False, logical="terminal.run"
    )
    executor = ToolExecutor(registry, _ApprovedTestHITL(), max_workers=2)
    markers = [tmp_path / "batch-run", tmp_path / "batch-launch"]
    calls = [
        GatewayToolCall(
            id=f"batch-{i}",
            function=FunctionCall(
                name=logical,
                arguments=json.dumps({"command": _sentinel_command(markers[i])}),
            ),
        )
        for i, logical in enumerate(TERMINAL_IDS)
    ]
    execution = ExecutionContext(
        conversation_id="offline-red-batch",
        execution_id="offline-red-batch-agent",
        session=object(),
    )
    original_popen = subprocess.Popen
    with patch("subprocess.Popen", wraps=original_popen) as host_popen:
        results = executor.execute_tools_parallel(
            calls, session=execution.session, execution_context=execution
        )
        count = host_popen.call_count
    assert len(results) == 2
    assert count == 0, "RED P0: parallel legacy Agent tool batch spawned host process"
    assert not any(marker.exists() for marker in markers)
    assert all(
        item.is_error and "AGENT_TOOL_POLICY_DENIED" in str(item.content)
        for item in results
    )
