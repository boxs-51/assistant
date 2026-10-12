"""Fail-closed sandbox-root quarantine with real hard-restart coverage.

This is a negative cleanup barrier, not positive VERIFIED_EMPTY or OS tree
reattachment proof. It deliberately cannot turn native effects into SUCCESS.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
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
    marker = manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id
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
    marker = base / ".sbx3-native-quarantine" / root.name
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


def test_direct_private_remove_root_cannot_bypass_marker(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    manager.quarantine_native_tree(lease)
    with pytest.raises(NativeTreeUnverifiedError):
        manager._remove_root(lease.root)
    assert lease.root.is_dir()
    assert manager.current_for_execution("native-e").state.value == "ACTIVE"


def test_missing_or_replaced_quarantine_directory_never_means_empty(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    manager.quarantine_native_tree(lease)
    control = manager.base_root / ".sbx3-native-quarantine"
    shutil.rmtree(control)
    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert lease.root.exists()
    # An attacker-supplied symlink cannot redirect the control directory.
    replacement = tmp_path / "outside-control"
    replacement.mkdir()
    try:
        os.symlink(replacement, control, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("OS does not allow symlink creation")
    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert lease.root.exists()
    assert replacement.is_dir()


def test_dangling_quarantine_marker_is_not_a_verified_empty_receipt(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    marker = manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id
    try:
        os.symlink(tmp_path / "no-such-target", marker)
    except (OSError, NotImplementedError):
        pytest.skip("OS does not allow symlink creation")
    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert lease.root.is_dir()
    assert marker.is_symlink()


def test_noncanonical_root_cannot_be_deleted_with_private_helper(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    outside = tmp_path / "untrusted"
    outside.mkdir()
    (outside / "important.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(NativeTreeUnverifiedError):
        manager._remove_root(outside)
    assert (outside / "important.txt").read_text(encoding="utf-8") == "preserve"


def test_concurrent_quarantine_and_destroy_share_manager_lifecycle_lock(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    entered = Event()
    release = Event()
    original = manager._native_quarantine_marker

    def hold_enrollment(sandbox_id):
        entered.set()
        assert release.wait(10), "enrollment barrier timed out"
        return original(sandbox_id)

    def do_destroy():
        try:
            manager.destroy(lease)
        except NativeTreeUnverifiedError:
            return "QUARANTINED"
        return "WRONGLY_DESTROYED"

    with patch.object(manager, "_native_quarantine_marker", side_effect=hold_enrollment):
        with ThreadPoolExecutor(max_workers=2) as pool:
            enroll = pool.submit(manager.quarantine_native_tree, lease)
            assert entered.wait(10), "worker did not reach enrollment"
            destroy = pool.submit(do_destroy)
            release.set()
            enroll.result(timeout=12)
            assert destroy.result(timeout=12) == "QUARANTINED"
    assert lease.root.exists()


def test_deleted_marker_cannot_release_enrolled_root_in_same_worker(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    manager.quarantine_native_tree(lease)
    marker = manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id
    marker.unlink()
    with pytest.raises(NativeTreeUnverifiedError):
        manager.release_execution("native-e", owner_user_id="native-owner")
    assert lease.root.exists()


def test_restart_quarantines_every_orphan_even_when_marker_was_erased(tmp_path):
    base = (tmp_path / "roots").resolve()
    first = SandboxManager(base)
    lease = _lease(first)
    first.quarantine_native_tree(lease)
    (base / ".sbx3-native-quarantine" / lease.sandbox_id).unlink()
    restarted = SandboxManager(base)
    assert lease.sandbox_id in restarted._orphan_root_ids
    with pytest.raises(NativeTreeUnverifiedError):
        restarted._remove_root(lease.root)
    assert lease.root.exists()


def test_restart_missing_control_dir_with_orphan_is_hard_failure(tmp_path):
    base = (tmp_path / "roots").resolve()
    first = SandboxManager(base)
    lease = _lease(first)
    first.quarantine_native_tree(lease)
    shutil.rmtree(base / ".sbx3-native-quarantine")
    with pytest.raises(NativeTreeUnverifiedError):
        SandboxManager(base)
    assert lease.root.exists()


def test_recreated_control_directory_cannot_bypass_live_manager(tmp_path):
    manager = SandboxManager((tmp_path / "roots").resolve())
    lease = _lease(manager)
    manager.quarantine_native_tree(lease)
    control = manager.base_root / ".sbx3-native-quarantine"
    moved = tmp_path / "old-control"
    control.rename(moved)
    control.mkdir(mode=0o700)
    with pytest.raises(NativeTreeUnverifiedError):
        manager.destroy(lease)
    assert lease.root.exists()
