"""Native D2 RED-first: recognized process aliases and custody must fail closed.

Only synthetic tool-result metadata and disposable temp roots are used. These
tests do not launch native commands, approve execution, or certify OS isolation.
"""
from __future__ import annotations

import pytest

from se.src.runtimes.agent.native_receipt_verifier import (
    NativeReceiptUnavailable, require_native_receipt, require_native_record,
)
from se.src.runtimes.capability.native_tree_recovery_b0 import (
    NativeTreeHold, require_no_uncertified_agent_success,
)
from se.src.runtimes.capability.contracts.sandbox import SandboxProfile
from se.src.runtimes.capability.sandbox import (
    SandboxManager, NativeTreeUnverifiedError,
)


@pytest.mark.parametrize("name", (
    "plugin.terminal.run",
    "plugin.terminal.launch",
    "plugin.python.run",
))
def test_d2_alias_success_never_crosses_native_receipt_gate(name):
    with pytest.raises(NativeReceiptUnavailable, match="AE_NATIVE_RECEIPT_UNAVAILABLE"):
        require_native_receipt(name, True, phase="alias-result")
    with pytest.raises(NativeReceiptUnavailable):
        require_native_record(type("Record", (), {"capability_id": name, "success": True})(),
                              phase="alias-readthrough")
    with pytest.raises(NativeTreeHold, match="SBX3_NATIVE_RECEIPT_UNAVAILABLE"):
        require_no_uncertified_agent_success(name, True)


@pytest.mark.parametrize("name", (
    "terminal.run", "terminal.launch", "python.run",
    "plugin.terminal.run", "plugin.terminal.launch", "plugin.python.run",
))
def test_d2_error_outcomes_remain_reportable(name):
    require_native_receipt(name, False, phase="failed")
    require_native_receipt(name, None, phase="unknown")
    require_no_uncertified_agent_success(name, False)


def test_d2_restarted_manager_cannot_delete_orphaned_native_root(tmp_path):
    root = (tmp_path / "roots").resolve()
    manager = SandboxManager(root)
    lease = manager.acquire_for_execution(
        execution_id="d2-execution", owner_user_id="d2-owner",
        profile=SandboxProfile(profile_id="d2-profile"),
    )
    manager.quarantine_native_tree(lease)
    restarted = SandboxManager(root)
    assert lease.root.exists()
    with pytest.raises(NativeTreeUnverifiedError):
        restarted._remove_root(lease.root)
    assert lease.root.exists(), "unsafe cleanup removed an OS-unverified root"
