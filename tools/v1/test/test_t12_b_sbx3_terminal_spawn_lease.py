"""TV1-T12-B-D0: Tools-owned diagnostic only; NOT native OS security certification.

Scope: #371, independent one-ADD PRE-CLAIM
https://github.com/boxs-51/assistant/issues/371#issuecomment-6074397930

These checks never execute untrusted commands, launch a process, use real
secrets, open a network socket, or claim that the preexisting trusted DIRECT
TerminalTool enforces Agent sandbox process/file/network isolation.

Passing these tests establishes only the frozen logical Tools contract,
input fail-before-spawn validation, synthetic lease state/path behavior, and
honest source/backend availability. A separate independently released native
backend, security negative controls and CI Phase B are mandatory.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from se.src.runtimes.capability.contracts.sandbox import (
    SandboxLeaseState,
    SandboxNetworkMode,
    SandboxProfile,
    SandboxSecretsMode,
)
from se.src.runtimes.capability.sandbox import (
    SandboxError,
    SandboxLeaseStateError,
    SandboxManager,
    SandboxPathError,
)
from tools.v1 import terminal_tool as terminal_module
from tools.v1.terminal_tool import TerminalTool

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LEGACY_TERMINAL = _REPO_ROOT / "tools/v1/terminal_tool.py"
_NATIVE_PROCESS_BACKEND = (
    _REPO_ROOT / "se/src/runtimes/capability/sandbox_process.py"
)
_NATIVE_TERMINAL_DRIVER = (
    _REPO_ROOT / "se/src/runtimes/capability/drivers/sandbox_terminal_driver.py"
)


def test_d0_terminal_public_logical_contract_has_no_model_spawn_authority(
    record_property,
):
    """Neither frozen logical operation exposes a trusted lease or env."""
    exports = {
        item["id"]: item for item in terminal_module.TOOL_METADATA["exports"]
    }
    assert set(exports) == {"terminal.run", "terminal.launch"}
    for logical_id, action in (("terminal.run", "run"), ("terminal.launch", "launch")):
        item = exports[logical_id]
        assert item["version"] == "1.0"
        assert item["bind"] == {"action": action}
        schema = item["input_schema"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == ["command"]
        assert {"command", "cwd"} <= set(schema["properties"])
        assert not {
            "env",
            "spawn_authority",
            "sandbox_lease",
            "owner_user_id",
            "execution_id",
        }.intersection(schema["properties"])
    record_property("TOOLS_D0_PUBLIC_SCHEMA", "FROZEN_TRUSTED_DIRECT_COMPATIBILITY")
    record_property("TOOLS_D0_NATIVE_AGENT_ISOLATION", "NOT_PROVEN")


def test_d0_invalid_input_fails_before_any_legacy_process_spawn(tmp_path, record_property):
    """No real subprocess is started; only public validation is exercised."""
    tool = TerminalTool(default_timeout=2)
    root = str(tmp_path.resolve())
    with patch.object(terminal_module.subprocess, "Popen") as popen:
        for result in (
            tool.run("", cwd=root),
            tool.launch("\x00invalid", cwd=root),
            tool.run("synthetic-not-executed", cwd=str(tmp_path / "missing")),
            tool.launch("synthetic-not-executed", cwd=str(tmp_path / "missing")),
        ):
            assert result["ok"] is False
            assert result["error"]["code"] in {
                "INVALID_ARGUMENT",
                "TERMINAL_CWD_NOT_FOUND",
            }
        popen.assert_not_called()
    record_property("TOOLS_D0_PRE_SPAWN_VALIDATION", "LEGACY_ARGUMENTS_ONLY")
    record_property("TOOLS_D0_KERNEL_PRE_INSTRUCTION_FENCE", "NOT_PROVEN")


def test_d0_synthetic_lease_owner_cwd_and_cleanup(
    tmp_path: Path, record_property
):
    """All writes are owned by pytest tmp_path; no process/network activity."""
    manager = SandboxManager((tmp_path / "owned-sandboxes").resolve())
    profile = SandboxProfile(profile_id="tools-t12-b-d0-profile")
    lease = manager.acquire_for_execution(
        execution_id="tools-t12-b-d0-execution",
        owner_user_id="tools-t12-b-d0-owner",
        profile=profile,
    )
    root = lease.root
    assert lease.state is SandboxLeaseState.ACTIVE
    try:
        marker = manager.resolve_path(lease, "synthetic-marker.txt")
        assert marker.parent == root
        marker.write_text("test-owned-canary", encoding="utf-8")
        assert marker.read_text(encoding="utf-8") == "test-owned-canary"

        with pytest.raises(SandboxError, match="another owner"):
            manager.acquire_for_execution(
                execution_id="tools-t12-b-d0-execution",
                owner_user_id="tools-t12-b-other-owner",
                profile=profile,
            )
        with pytest.raises(SandboxPathError):
            manager.resolve_path(lease, "../escape.txt")
        with pytest.raises(SandboxPathError):
            manager.resolve_path(lease, r"C:\outside.txt")

        quiesced = manager.quiesce(lease)
        assert quiesced.state is SandboxLeaseState.QUIESCING
        with pytest.raises(SandboxLeaseStateError):
            manager.resolve_path(quiesced, "after-quiesce.txt")
    finally:
        destroyed = manager.release_execution(
            "tools-t12-b-d0-execution",
            owner_user_id="tools-t12-b-d0-owner",
        )
        assert destroyed is not None
        assert destroyed.state is SandboxLeaseState.DESTROYED
        assert not root.exists()
    record_property("TOOLS_D0_LEASE_IDENTITY", "SYNTHETIC_STATE_ONLY")
    record_property("TOOLS_D0_NATIVE_DESCENDANT_CLEANUP", "NOT_PROVEN")


def test_d0_default_deny_profile_is_declarative_not_native(record_property):
    profile = SandboxProfile(profile_id="tools-t12-b-d0-defaults")
    assert profile.network.mode is SandboxNetworkMode.NONE
    assert profile.network.allowed_hosts == ()
    assert profile.secrets.mode is SandboxSecretsMode.NONE
    assert profile.secrets.allowed_names == ()
    assert profile.process.environment_allowlist == ()
    assert profile.process.allowed_interpreters == ()
    record_property("TOOLS_D0_NETWORK_NONE", "DECLARATIVE_NOT_OS_ENFORCED")
    record_property("TOOLS_D0_WINDOWS_EGRESS_NONE", "NOT_PROVEN")
    record_property("TOOLS_D0_LINUX_NATIVE_PROCESS_FENCE", "NOT_PROVEN")


def test_d0_native_backend_source_availability_is_only_observation(record_property):
    """Absence is NOT_IMPLEMENTED, not a measured real-sandbox bypass."""
    sources = {
        "native_process_backend": _NATIVE_PROCESS_BACKEND.is_file(),
        "native_terminal_driver": _NATIVE_TERMINAL_DRIVER.is_file(),
    }
    record_property("TOOLS_D0_SOURCE_OBSERVATION", str(sources))
    record_property("TOOLS_D0_NATIVE_OS_SECURITY", "NOT_IMPLEMENTED/NOT_PROVEN")
    assert sources == {
        "native_process_backend": False,
        "native_terminal_driver": False,
    }, "Native sources changed: independently review D0 successor before reuse"


def test_d0_trusted_direct_popen_observation_is_not_an_agent_escape(record_property):
    """Inspect AST/kwargs without calling subprocess or changing host files."""
    tree = ast.parse(_LEGACY_TERMINAL.read_text(encoding="utf-8"))
    popen_sites = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == "Popen"
    ]
    assert len(popen_sites) >= 2, "Trusted DIRECT spawn sites changed; re-audit"
    record_property("TOOLS_D0_TRUSTED_DIRECT_POPEN_LINES", str(sorted(popen_sites)))
    record_property("TOOLS_D0_AGENT_SANDBOX_ESCAPE", "NOT_TESTED/NOT_PROVEN")
