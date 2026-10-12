"""Real Linux/Windows separate-process root quarantine/delete interlocks.

Negative safety evidence ONLY; there is no authenticated VERIFIED_EMPTY
production release path. Marker creation and full root delete are serialized
by one OS-held native custody lock, not only per-manager thread locks.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from se.src.runtimes.capability.contracts.sandbox import SandboxProfile
from se.src.runtimes.capability.native_root_custody_b0 import (
    NativeCustodyUnavailable,
)
from se.src.runtimes.capability.sandbox import (
    NativeTreeUnverifiedError,
    SandboxManager,
)


def _ready(path: Path, *, timeout: float = 12) -> None:
    limit = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < limit:
        time.sleep(0.025)
    assert path.exists(), f"child did not reach OS-lock fault cut: {path}"


def _new_lease(tmp_path):
    base = (tmp_path / "native-roots").resolve()
    manager = SandboxManager(base)
    lease = manager.acquire_for_execution(
        execution_id="crossprocess-execution",
        owner_user_id="crossprocess-owner",
        profile=SandboxProfile(profile_id="native-default"),
    )
    return manager, lease


def _enrollment_worker(manager, lease, *, entering, release) -> str:
    # Test-only rehydrate of an in-memory lease after a second manager started.
    # Production recovery MUST use durable OS-broker custody instead.
    return (
        "import pathlib, time, sys\n"
        "from se.src.runtimes.capability.sandbox import SandboxManager\n"
        "from se.src.runtimes.capability.contracts.sandbox import SandboxLease\n"
        f"m=SandboxManager(pathlib.Path({str(manager.base_root)!r}))\n"
        f"lease=SandboxLease.model_validate_json({lease.model_dump_json()!r})\n"
        "m._leases[lease.sandbox_id]=lease\n"
        "m._execution_leases[lease.execution_id]=lease.sandbox_id\n"
        f"pathlib.Path({str(entering)!r}).write_text('entering')\n"
        "original=m._native_quarantine_marker\n"
        "def during_locked_enrollment(name):\n"
        f"    pathlib.Path({str(release)!r}).parent.mkdir(exist_ok=True, parents=True)\n"
        "    return original(name)\n"
        "m._native_quarantine_marker=during_locked_enrollment\n"
        "try:\n"
        "    m.quarantine_native_tree(lease)\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__, flush=True)\n"
        "    sys.exit(19)\n"
    )


def test_cross_process_enroll_first_blocks_direct_root_delete(tmp_path):
    manager, lease = _new_lease(tmp_path)
    ready = tmp_path / "worker-holds-lock"
    release = tmp_path / "release-worker"
    script = (
        "import pathlib, time\n"
        "from se.src.runtimes.capability.sandbox import SandboxManager\n"
        "from se.src.runtimes.capability.contracts.sandbox import SandboxLease\n"
        f"m=SandboxManager(pathlib.Path({str(manager.base_root)!r}))\n"
        f"lease=SandboxLease.model_validate_json({lease.model_dump_json()!r})\n"
        "m._leases[lease.sandbox_id]=lease\n"
        "original=m._native_quarantine_marker\n"
        "def hold_before_marker(name):\n"
        f"    pathlib.Path({str(ready)!r}).write_text('locked')\n"
        f"    while not pathlib.Path({str(release)!r}).exists():\n"
        "        time.sleep(.02)\n"
        "    return original(name)\n"
        "m._native_quarantine_marker=hold_before_marker\n"
        "m.quarantine_native_tree(lease)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _ready(ready)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(manager._remove_root, lease.root)
            time.sleep(.2)
            assert not future.done(), "root delete bypassed external native enrollment"
            release.touch()
            with pytest.raises(NativeTreeUnverifiedError):
                future.result(timeout=12)
        stdout, stderr = proc.communicate(timeout=12)
        assert proc.returncode == 0, stderr.decode("utf-8", "replace")[-1400:]
        assert lease.root.is_dir()
        assert (manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id).is_file()
    finally:
        release.touch(exist_ok=True)
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)


def test_cross_process_delete_first_denies_late_enrollment(tmp_path):
    manager, lease = _new_lease(tmp_path)
    inside_delete = tmp_path / "inside-delete-lock"
    allow_delete = tmp_path / "allow-delete"
    worker_started = tmp_path / "enroller-started"
    orig = manager._require_native_root_empty

    def deleting_while_holding_os_lock(root):
        inside_delete.write_text("locked")
        _ready(allow_delete)
        return orig(root)

    with patch.object(
        manager, "_require_native_root_empty", side_effect=deleting_while_holding_os_lock
    ):
        with ThreadPoolExecutor(max_workers=1) as pool:
            deletion = pool.submit(manager._remove_root, lease.root)
            _ready(inside_delete)
            script = _enrollment_worker(
                manager, lease, entering=worker_started, release=allow_delete
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _ready(worker_started)
                time.sleep(.2)
                assert not (
                    manager.base_root / ".sbx3-native-quarantine" / lease.sandbox_id
                ).exists()
                allow_delete.touch()
                deletion.result(timeout=12)
                stdout, stderr = proc.communicate(timeout=12)
                assert proc.returncode == 19, stderr.decode("utf-8", "replace")
                assert b"NativeTreeUnverifiedError" in stdout
                assert not lease.root.exists()
            finally:
                allow_delete.touch(exist_ok=True)
                if proc.poll() is None:
                    proc.kill()
                proc.communicate(timeout=10)


def test_hard_exit_releases_os_lock_but_not_quarantine(tmp_path):
    manager, lease = _new_lease(tmp_path)
    manager.quarantine_native_tree(lease)
    ready = tmp_path / "locked-then-exit"
    script = (
        "import os, pathlib\n"
        "from se.src.runtimes.capability.sandbox import SandboxManager\n"
        f"m=SandboxManager(pathlib.Path({str(manager.base_root)!r}))\n"
        "with m._root_custody.hold():\n"
        f"    pathlib.Path({str(ready)!r}).write_text('locked')\n"
        "    os._exit(88)\n"
    )
    worker = subprocess.run(
        [sys.executable, "-c", script],
        timeout=15, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert worker.returncode == 88, worker.stderr.decode("utf-8", "replace")
    _ready(ready)
    with pytest.raises(NativeTreeUnverifiedError):
        manager._remove_root(lease.root)
    assert lease.root.exists()


def test_replaced_os_lock_inode_fails_closed(tmp_path):
    manager, lease = _new_lease(tmp_path)
    lockpath = manager.base_root / ".sbx3-native-quarantine" / ".root-lifecycle-lock"
    displaced = tmp_path / "old-lock"
    lockpath.rename(displaced)
    lockpath.touch()
    with pytest.raises(NativeCustodyUnavailable):
        manager._remove_root(lease.root)
    assert lease.root.exists()


def test_legacy_non_native_cleanup_remains_operational(tmp_path):
    manager, lease = _new_lease(tmp_path)
    destroyed = manager.release_execution(
        lease.execution_id, owner_user_id=lease.owner_user_id
    )
    assert destroyed.state.value == "DESTROYED"
    assert not lease.root.exists()
