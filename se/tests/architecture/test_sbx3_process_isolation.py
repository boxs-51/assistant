"""SBX-3 OS-D0: bounded, non-executing discovery; NOT native isolation proof.

Historical #164 one-ADD D0 PRE-CLAIM (before active Policy #85 v2.5):
https://github.com/boxs-51/assistant/issues/164#issuecomment-6073330338
Owner historical exact-path D0 CLAIM:
https://github.com/boxs-51/assistant/issues/164#issuecomment-6073351305

These tests deliberately NEVER invoke a legacy host shell, run an Agent command,
open a socket, read real environment secrets, or claim that a declarative
SandboxProfile enforces kernel/Windows process and network restrictions.

A green D0 run means *diagnostic baseline only*. It MUST NOT be counted as
Linux/Windows pre-first-untrusted-instruction or network NONE certification.
Native process containment requires a separately released production stage,
native negative controls, and CI Phase B real OS runners/selectors.
"""

from __future__ import annotations

import ast
from pathlib import Path

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
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NATIVE_SE_BACKEND = (
    _REPO_ROOT / "se/src/runtimes/capability/sandbox_process.py"
)
_NATIVE_SE_TERMINAL_DRIVER = (
    _REPO_ROOT / "se/src/runtimes/capability/drivers/sandbox_terminal_driver.py"
)
_TOOLS_TERMINAL = _REPO_ROOT / "tools/v1/terminal_tool.py"


def test_d0_profile_default_deny_is_declarative_not_native_enforcement(
    record_property,
):
    profile = SandboxProfile(profile_id="sbx3-d0-default-deny")
    assert profile.network.mode is SandboxNetworkMode.NONE
    assert profile.network.allowed_hosts == ()
    assert profile.secrets.mode is SandboxSecretsMode.NONE
    assert profile.secrets.allowed_names == ()
    assert profile.process.environment_allowlist == ()
    assert profile.process.allowed_interpreters == ()
    record_property("SBX3_D0_PROFILE_STATUS", "DECLARATIVE_ONLY")
    record_property("SBX3_NATIVE_PROCESS_NETWORK_PROOF", "NOT_PROVEN")


def test_d0_temp_only_lease_canary_enforces_owner_and_cleanup(
    tmp_path: Path, record_property
):
    """No subprocess, sockets, real secrets or writes outside pytest tmp_path."""
    manager = SandboxManager((tmp_path / "sandboxes").resolve())
    profile = SandboxProfile(profile_id="sbx3-d0-synthetic")
    lease = manager.acquire_for_execution(
        execution_id="sbx3-d0-execution",
        owner_user_id="sbx3-d0-test-owner",
        profile=profile,
    )
    canary = manager.resolve_path(lease, "synthetic-canary.txt")
    try:
        canary.write_text("test-owned-only", encoding="utf-8")
        assert canary.read_text(encoding="utf-8") == "test-owned-only"
        with pytest.raises(SandboxError, match="another owner"):
            manager.acquire_for_execution(
                execution_id="sbx3-d0-execution",
                owner_user_id="sbx3-d0-foreign-owner",
                profile=profile,
            )
        quiescing = manager.quiesce(lease)
        assert quiescing.state is SandboxLeaseState.QUIESCING
        with pytest.raises(SandboxLeaseStateError):
            manager.resolve_path(quiescing, "after-quiesce.txt")
    finally:
        destroyed = manager.release_execution(
            "sbx3-d0-execution",
            owner_user_id="sbx3-d0-test-owner",
        )
        assert destroyed is not None
        assert destroyed.state is SandboxLeaseState.DESTROYED
        assert not lease.root.exists()
    record_property("SBX3_D0_LEASE_STATUS", "SYNTHETIC_ONLY")
    record_property("SBX3_NATIVE_DESCENDANT_CLEANUP_PROOF", "NOT_PROVEN")


def test_d0_native_process_backend_is_unimplemented_on_frozen_base(
    record_property,
):
    """A1 three-state gate: source presence cannot certify native isolation."""
    observed = {
        "sandbox_process": _NATIVE_SE_BACKEND.is_file(),
        "sandbox_terminal_driver": _NATIVE_SE_TERMINAL_DRIVER.is_file(),
    }
    present_count = sum(observed.values())
    record_property("SBX3_D0_SOURCE_OBSERVATION", str(observed))
    record_property("SBX3_NATIVE_OS_CONFINEMENT", "NOT_PROVEN")
    record_property("SBX3_CI417_C2", "NOT_ELIGIBLE")

    if present_count == 0:
        # Portable D0 diagnostics remain GREEN, but native security is absent.
        record_property("SBX3_D0_NATIVE_STATE", "NOT_IMPLEMENTED")
        record_property("SBX3_NATIVE_OS_CONFINEMENT", "NOT_IMPLEMENTED/NOT_PROVEN")
        return

    if present_count == 1:
        # Do not canonically land an incomplete process/terminal native pair.
        record_property("SBX3_D0_NATIVE_STATE", "PARTIAL_NATIVE_INSTALLATION")
        pytest.fail(
            "PARTIAL_NATIVE_INSTALLATION: exactly one native sandbox source "
            "exists; deny Agent terminal dispatch and complete independent "
            "source/test review before any integration",
            pytrace=False,
        )

    # Both filenames existing is not Linux/Windows kernel confinement proof.
    # A separately released B0 native-security test successor must supersede
    # this fail-closed gate only after actual supported-host evidence passes.
    record_property("SBX3_D0_NATIVE_STATE", "NATIVE_OS_SECURITY_NOT_CERTIFIED")
    pytest.fail(
        "NATIVE_OS_SECURITY_NOT_CERTIFIED: both native sources are present "
        "but pre-first-instruction Linux/Windows process, filesystem, network "
        "NONE, owner/lease/revoke and descendant cleanup remain unproven",
        pytrace=False,
    )


def test_d0_legacy_terminal_host_spawn_remains_outside_native_proof(
    record_property,
):
    """Inspect source without importing or executing the host TerminalTool."""
    tree = ast.parse(_TOOLS_TERMINAL.read_text(encoding="utf-8"))
    legacy_popen_sites = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert legacy_popen_sites, (
        "Host TerminalTool spawn surface changed; re-review the SBX-3 "
        "boundary rather than treating the legacy tool as a sandbox"
    )
    record_property("SBX3_D0_LEGACY_SPAWN_LINES", str(legacy_popen_sites))
    record_property("SBX3_NATIVE_PRE_INSTRUCTION_FENCE", "NOT_PROVEN")
