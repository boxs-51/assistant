"""Fail-closed sandbox-root quarantine with real hard-restart coverage.

This is a negative cleanup barrier, not positive VERIFIED_EMPTY or OS tree
reattachment proof. It deliberately cannot turn native effects into SUCCESS.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from se.src.runtimes.capability.contracts.sandbox import SandboxProfile
from se.src.runtimes.capability.sandbox import (
    NativeTreeUnverifiedError,
    SandboxManager,
)


def _lease(manager: SandboxManager):
    return manager.acquire_for_execution(
        execution_id="native-e",
        owner_user_id="native-owner",
        profile=SandboxProfile(profile_id="native-profile"),
    )


def test_enrolled_native_root_cannot_be_destroyed_or_released(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    marker = lease.root / ".sbx3-native-tree-unverified"
    manager.quarantine_native_tree(lease)
    assert marker.exists()
    with pytest.raises(FileExistsError):
        manager.quarantine_native_tree(lease)
    with pytest.raises(NativeTreeUnverifiedError):
        manager.release_execution("native-e", owner_user_id="native-owner")
    assert manager.current_for_execution("native-e").state.value == "CLEANUP"
    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert lease.root.exists()
    assert marker.exists()


def test_real_worker_hard_exit_preserves_quarantine_across_restart(tmp_path):
    base = (tmp_path / "roots").resolve()
    root_export = tmp_path / "created-root.txt"
    script = (
        "import os, pathlib\n"
        "from se.src.runtimes.capability.sandbox import SandboxManager\n"
        "from se.src.runtimes.capability.contracts.sandbox import SandboxProfile\n"
        f"m=SandboxManager(pathlib.Path({str(base)!r}))\n"
        "lease=m.acquire_for_execution(execution_id='native-e',"
        "owner_user_id='native-owner',"
        "profile=SandboxProfile(profile_id='native-profile'))\n"
        "m.quarantine_native_tree(lease)\n"
        f"pathlib.Path({str(root_export)!r}).write_text(str(lease.root))\n"
        "os._exit(77)\n"
    )
    child = subprocess.run([sys.executable, "-c", script], timeout=20, check=False)
    assert child.returncode == 77
    root = Path(root_export.read_text())
    marker = root / ".sbx3-native-tree-unverified"
    assert marker.is_file()
    replacement_manager = SandboxManager(base)
    # Recreated manager has empty in-memory lease registry. It must not erase
    # the orphan root, claim a positive OS proof or reuse its generation.
    assert replacement_manager._leases == {}
    with pytest.raises(NativeTreeUnverifiedError):
        replacement_manager._require_native_root_empty(root)
    assert root.exists()
    assert marker.exists()


def test_legacy_non_native_cleanup_preserved(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    assert manager.release_execution("native-e", owner_user_id="native-owner").state.value == "DESTROYED"
    assert not lease.root.exists()
