from __future__ import annotations

import os
from pathlib import Path

import pytest

from se.src.runtimes.capability.contracts.sandbox import (
    SandboxLeaseState,
    SandboxNetworkMode,
    SandboxProfile,
)
from se.src.runtimes.capability.sandbox import (
    SandboxLeaseStateError,
    SandboxManager,
    SandboxPathError,
)


def _manager(tmp_path: Path) -> SandboxManager:
    return SandboxManager((tmp_path / "sandbox-root").resolve())


def _profile() -> SandboxProfile:
    return SandboxProfile(profile_id="default-deny")


def test_acquire_binds_unique_execution_owned_roots_and_default_deny_profile(
    tmp_path,
):
    manager = _manager(tmp_path)
    first = manager.acquire(
        execution_id="exec-1",
        owner_user_id="user-1",
        profile=_profile(),
    )
    second = manager.acquire(
        execution_id="exec-2",
        owner_user_id="user-1",
        profile=_profile(),
    )

    assert first.state is SandboxLeaseState.ACTIVE
    assert first.execution_id == "exec-1"
    assert first.owner_user_id == "user-1"
    assert first.root.parent == manager.base_root
    assert first.root != second.root
    assert first.root.exists()
    assert second.root.exists()

    profile = manager.profile_for(first)
    assert profile.network.mode is SandboxNetworkMode.NONE
    assert profile.secrets.deny_by_default is True
    assert profile.secrets.allowed_names == ()


def test_legal_lifecycle_cleanup_is_deterministic_and_destroyed_lease_is_dead(
    tmp_path,
):
    manager = _manager(tmp_path)
    lease = manager.acquire(
        execution_id="exec-life",
        owner_user_id="user-life",
        profile=_profile(),
    )
    payload = manager.resolve_path(lease, "nested/result.txt")
    payload.parent.mkdir(parents=True)
    payload.write_text("ephemeral", encoding="utf-8")

    with pytest.raises(SandboxLeaseStateError):
        manager.begin_cleanup(lease)

    quiescing = manager.quiesce(lease)
    assert quiescing.state is SandboxLeaseState.QUIESCING
    cleaning = manager.begin_cleanup(quiescing)
    assert cleaning.state is SandboxLeaseState.CLEANUP

    destroyed = manager.destroy(cleaning)
    assert destroyed.state is SandboxLeaseState.DESTROYED
    assert not destroyed.root.exists()

    again = manager.destroy(destroyed)
    assert again.state is SandboxLeaseState.DESTROYED

    with pytest.raises(SandboxLeaseStateError):
        manager.resolve_path(destroyed, "after-destroy.txt")


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape.txt",
        "..\\escape.txt",
        "/tmp/escape.txt",
        "C:\\escape.txt",
        "\\\\server\\share\\escape.txt",
        "nul\x00byte.txt",
    ],
)
def test_path_resolution_rejects_absolute_parent_and_invalid_paths(
    tmp_path,
    unsafe,
):
    manager = _manager(tmp_path)
    lease = manager.acquire(
        execution_id="exec-path",
        owner_user_id="user-path",
        profile=_profile(),
    )

    with pytest.raises(SandboxPathError):
        manager.resolve_path(lease, unsafe)


def test_path_resolution_stays_inside_lease_root(tmp_path):
    manager = _manager(tmp_path)
    lease = manager.acquire(
        execution_id="exec-contained",
        owner_user_id="user-contained",
        profile=_profile(),
    )

    candidate = manager.resolve_path(lease, "a/b/c.txt")
    candidate.relative_to(lease.root.resolve())


def test_symlink_or_reparse_escape_is_rejected_when_platform_supports_it(
    tmp_path,
):
    manager = _manager(tmp_path)
    lease = manager.acquire(
        execution_id="exec-link",
        owner_user_id="user-link",
        profile=_profile(),
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    link = lease.root / "outside-link"

    try:
        os.symlink(
            outside,
            link,
            target_is_directory=True,
        )
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink/reparse creation unsupported: {exc}")

    with pytest.raises(SandboxPathError):
        manager.resolve_path(lease, "outside-link/escape.txt")


def test_destroy_from_active_performs_ordered_cleanup_idempotently(tmp_path):
    manager = _manager(tmp_path)
    lease = manager.acquire(
        execution_id="exec-direct-cleanup",
        owner_user_id="user-direct-cleanup",
        profile=_profile(),
    )
    manager.resolve_path(lease, "file.txt").write_text(
        "data",
        encoding="utf-8",
    )

    destroyed = manager.destroy(lease)
    assert destroyed.state is SandboxLeaseState.DESTROYED
    assert not destroyed.root.exists()
    assert manager.destroy(destroyed) == destroyed
