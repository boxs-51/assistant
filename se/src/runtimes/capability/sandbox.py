from __future__ import annotations

import os
import shutil
import stat
from functools import wraps
from threading import RLock
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from .contracts.sandbox import (
    SandboxLease,
    SandboxLeaseState,
    SandboxProfile,
)


class SandboxError(RuntimeError):
    pass


class SandboxLeaseNotFoundError(SandboxError):
    pass


class SandboxLeaseStateError(SandboxError):
    pass


class SandboxPathError(SandboxError):
    pass


class NativeTreeUnverifiedError(SandboxError):
    """A native tree cannot be proven empty; retain its sandbox root."""


def _lifecycle_serialized(method):
    """Serialize manager-local spawn enrollment and destructive transitions."""
    @wraps(method)
    def invoke(self, *args, **kwargs):
        with self._lifecycle_lock:
            return method(self, *args, **kwargs)
    return invoke


class SandboxManager:
    """Own execution-scoped ephemeral roots without granting host-path authority."""

    def __init__(self, base_root: Path | str) -> None:
        raw_base = Path(base_root).expanduser()
        if not raw_base.is_absolute():
            raise ValueError("sandbox base_root must be an explicit absolute path")
        self._base_root = raw_base.resolve(strict=False)
        if self._base_root in {Path.cwd().resolve(), Path.home().resolve()}:
            raise ValueError("sandbox base_root cannot be ambient cwd or user home")
        self._base_root.mkdir(parents=True, exist_ok=True)
        self._lifecycle_lock = RLock()
        # Existing roots have no authenticated in-memory lease on a new
        # worker. Treat ALL of them as unknown, even if a marker was removed.
        self._orphan_root_ids = frozenset(
            entry.name for entry in self._base_root.iterdir()
            if entry.name.startswith("sbx-")
        )
        self._enrolled_native_ids: set[str] = set()
        # Quarantine is a manager-private sibling of sandbox roots, not a
        # child-writable path. Once created it must never be auto-repaired
        # following deletion, symlink or reparse-point replacement.
        self._quarantine_dir = self._base_root / ".sbx3-native-quarantine"
        if self._quarantine_dir.is_symlink() or self._is_junction(self._quarantine_dir):
            raise NativeTreeUnverifiedError("native quarantine path is redirected")
        # A vanished control directory alongside orphan execution roots is
        # ambiguous after restart: never recreate it and erase the evidence.
        if not self._quarantine_dir.exists() and any(
            entry.name.startswith("sbx-") for entry in self._base_root.iterdir()
        ):
            raise NativeTreeUnverifiedError(
                "quarantine registry missing alongside existing sandbox roots"
            )
        self._quarantine_dir.mkdir(mode=0o700, exist_ok=True)
        control_stat = self._quarantine_dir.stat()
        self._quarantine_identity = (control_stat.st_dev, control_stat.st_ino)
        self._require_quarantine_dir()
        self._leases: dict[str, SandboxLease] = {}
        self._profiles: dict[str, SandboxProfile] = {}
        self._execution_leases: dict[str, str] = {}

    @property
    def base_root(self) -> Path:
        return self._base_root

    @_lifecycle_serialized
    def acquire(
        self,
        *,
        execution_id: str,
        owner_user_id: str,
        profile: SandboxProfile,
    ) -> SandboxLease:
        sandbox_id = f"sbx-{uuid4().hex}"
        root = self._base_root / sandbox_id
        root.mkdir(mode=0o700, parents=False, exist_ok=False)

        created = SandboxLease(
            sandbox_id=sandbox_id,
            execution_id=execution_id,
            owner_user_id=owner_user_id,
            profile_id=profile.profile_id,
            root=root,
            state=SandboxLeaseState.CREATED,
        )
        active = created.model_copy(update={"state": SandboxLeaseState.ACTIVE})
        existing_profile = self._profiles.get(profile.profile_id)
        if existing_profile is not None and existing_profile != profile:
            self._remove_root(root)
            raise SandboxError(
                "sandbox profile_id is already bound to different semantics"
            )
        self._profiles.setdefault(profile.profile_id, profile)
        self._leases[sandbox_id] = active
        return active

    @_lifecycle_serialized
    def acquire_for_execution(
        self,
        *,
        execution_id: str,
        owner_user_id: str,
        profile: SandboxProfile,
    ) -> SandboxLease:
        """Acquire or reuse exactly one active lease for one execution."""
        existing_id = self._execution_leases.get(execution_id)
        if existing_id is not None:
            existing = self._leases.get(existing_id)
            if (
                existing is None
                or existing.state is SandboxLeaseState.DESTROYED
            ):
                self._execution_leases.pop(execution_id, None)
            else:
                if existing.execution_id != execution_id:
                    raise SandboxLeaseNotFoundError(
                        "sandbox execution identity mismatch"
                    )
                if existing.owner_user_id != owner_user_id:
                    raise SandboxError(
                        "sandbox execution is already bound to another owner"
                    )
                if existing.profile_id != profile.profile_id:
                    raise SandboxError(
                        "sandbox execution is already bound to another profile"
                    )
                bound_profile = self._profiles.get(existing.profile_id)
                if bound_profile != profile:
                    raise SandboxError(
                        "sandbox execution profile semantics changed"
                    )
                if existing.state is not SandboxLeaseState.ACTIVE:
                    raise SandboxLeaseStateError(
                        "sandbox execution lease is not active"
                    )
                return existing

        lease = self.acquire(
            execution_id=execution_id,
            owner_user_id=owner_user_id,
            profile=profile,
        )
        self._execution_leases[execution_id] = lease.sandbox_id
        return lease

    def current_for_execution(self, execution_id: str) -> SandboxLease:
        sandbox_id = self._execution_leases.get(execution_id)
        if sandbox_id is None:
            raise SandboxLeaseNotFoundError(execution_id)
        stored = self._leases.get(sandbox_id)
        if stored is None:
            raise SandboxLeaseNotFoundError(sandbox_id)
        if stored.execution_id != execution_id:
            raise SandboxLeaseNotFoundError(
                "sandbox execution identity mismatch"
            )
        return stored

    # Native B0 is not production-wired. This marker is an irreversible
    # quarantine tombstone, NOT an OS VERIFIED_EMPTY receipt. A future
    # authenticated OS broker must own clearing it with a generation fence.
    _NATIVE_QUARANTINE_MARKER = ".sbx3-native-tree-unverified"

    @_lifecycle_serialized
    def quarantine_native_tree(self, lease: SandboxLease) -> None:
        """Durably reject root deletion if any native process might exist.

        Must execute before any experimental native dispatch. Creation is
        exclusive, and the tombstone survives application/worker restart.
        The marker carries no positive authority and never enables replay.
        """
        current = self.current(lease)
        if current.state is not SandboxLeaseState.ACTIVE:
            raise SandboxLeaseStateError("native enrollment requires ACTIVE lease")
        self._assert_owned_root(current, current.root.resolve(strict=False))
        marker = self._native_quarantine_marker(current.sandbox_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(marker, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(current.sandbox_id + "\\n")
                file.flush()
                os.fsync(file.fileno())
            # Persist the directory entry too on platforms supporting it.
            if os.name == "posix":
                directory_fd = os.open(marker.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except BaseException:
            # A partial marker is STILL quarantined: never delete it.
            raise
        self._enrolled_native_ids.add(current.sandbox_id)

    @staticmethod
    def _is_junction(path: Path) -> bool:
        predicate = getattr(path, "is_junction", None)
        return bool(callable(predicate) and predicate())

    def _require_quarantine_dir(self) -> None:
        directory = self._quarantine_dir
        if (
            directory.is_symlink()
            or self._is_junction(directory)
            or not directory.is_dir()
            or directory.resolve(strict=False) != directory
        ):
            raise NativeTreeUnverifiedError(
                "trusted quarantine directory is absent or redirected"
            )
        control = directory.stat()
        if (
            (control.st_dev, control.st_ino) != self._quarantine_identity
            or (os.name == "posix" and
                bool(control.st_mode & (stat.S_IWGRP | stat.S_IWOTH)))
        ):
            raise NativeTreeUnverifiedError(
                "quarantine registry identity or permissions changed"
            )

    def _native_quarantine_marker(self, sandbox_id: str) -> Path:
        if (
            not sandbox_id.startswith("sbx-")
            or len(sandbox_id) != 36
            or any(letter not in "0123456789abcdef" for letter in sandbox_id[4:])
        ):
            raise NativeTreeUnverifiedError("non-canonical sandbox root identity")
        self._require_quarantine_dir()
        return self._quarantine_dir / sandbox_id

    def _require_native_root_empty(self, root: Path) -> None:
        if root.parent != self._base_root or root.is_symlink() or self._is_junction(root):
            raise NativeTreeUnverifiedError("sandbox root is not a trusted direct child")
        if root.name in self._enrolled_native_ids or root.name in self._orphan_root_ids:
            raise NativeTreeUnverifiedError(
                "native enrollment or orphan root requires OS-owned verification"
            )
        marker = self._native_quarantine_marker(root.name)
        # lstat catches dangling symlinks and denies on all IO/permission errors.
        try:
            marker.lstat()
        except FileNotFoundError:
            return
        raise NativeTreeUnverifiedError(
            "native tree quarantined: trusted OS VERIFIED_EMPTY absent"
        )

    @_lifecycle_serialized
    def release_execution(
        self,
        execution_id: str,
        *,
        owner_user_id: str | None = None,
    ) -> SandboxLease | None:
        sandbox_id = self._execution_leases.get(execution_id)
        if sandbox_id is None:
            return None
        stored = self._leases.get(sandbox_id)
        if stored is None:
            self._execution_leases.pop(execution_id, None)
            return None
        if stored.execution_id != execution_id:
            raise SandboxLeaseNotFoundError(
                "sandbox execution identity mismatch"
            )
        if (
            owner_user_id is not None
            and stored.owner_user_id != owner_user_id
        ):
            raise SandboxError(
                "sandbox execution is bound to another owner"
            )
        return self.destroy(stored)

    def current(self, lease: SandboxLease) -> SandboxLease:
        stored = self._leases.get(lease.sandbox_id)
        if stored is None:
            if lease.state is SandboxLeaseState.DESTROYED:
                expected_root = self._base_root / lease.sandbox_id
                if (
                    lease.root == expected_root
                    and not lease.root.exists()
                    and not lease.root.is_symlink()
                ):
                    return lease
            raise SandboxLeaseNotFoundError(lease.sandbox_id)
        if (
            stored.identity != lease.identity
            or stored.root != lease.root
        ):
            raise SandboxLeaseNotFoundError("sandbox lease identity mismatch")
        return stored

    def profile_for(self, lease: SandboxLease) -> SandboxProfile:
        current = self.current(lease)
        profile = self._profiles.get(current.profile_id)
        if profile is None:
            raise SandboxLeaseNotFoundError("sandbox profile is unavailable")
        return profile

    @_lifecycle_serialized
    def quiesce(self, lease: SandboxLease) -> SandboxLease:
        current = self.current(lease)
        if current.state is SandboxLeaseState.QUIESCING:
            return current
        if current.state is not SandboxLeaseState.ACTIVE:
            raise SandboxLeaseStateError(
                f"cannot quiesce sandbox from {current.state.value}"
            )
        updated = current.model_copy(
            update={"state": SandboxLeaseState.QUIESCING}
        )
        self._leases[current.sandbox_id] = updated
        return updated

    @_lifecycle_serialized
    def begin_cleanup(self, lease: SandboxLease) -> SandboxLease:
        current = self.current(lease)
        if current.state is SandboxLeaseState.CLEANUP:
            return current
        if current.state is not SandboxLeaseState.QUIESCING:
            raise SandboxLeaseStateError(
                f"cannot clean sandbox from {current.state.value}"
            )
        updated = current.model_copy(update={"state": SandboxLeaseState.CLEANUP})
        self._leases[current.sandbox_id] = updated
        return updated

    @_lifecycle_serialized
    def destroy(self, lease: SandboxLease) -> SandboxLease:
        current = self.current(lease)
        if current.state is SandboxLeaseState.DESTROYED:
            return current
        if current.state is SandboxLeaseState.ACTIVE:
            current = self.quiesce(current)
        if current.state is SandboxLeaseState.QUIESCING:
            current = self.begin_cleanup(current)
        if current.state is not SandboxLeaseState.CLEANUP:
            raise SandboxLeaseStateError(
                f"cannot destroy sandbox from {current.state.value}"
            )

        self._require_native_root_empty(current.root)
        self._remove_root(current.root)
        destroyed = current.model_copy(
            update={"state": SandboxLeaseState.DESTROYED}
        )
        self._leases.pop(current.sandbox_id, None)
        if (
            self._execution_leases.get(current.execution_id)
            == current.sandbox_id
        ):
            self._execution_leases.pop(current.execution_id, None)
        return destroyed

    def resolve_path(
        self,
        lease: SandboxLease,
        relative_path: str | Path,
    ) -> Path:
        current = self.current(lease)
        if current.state is not SandboxLeaseState.ACTIVE:
            raise SandboxLeaseStateError(
                f"sandbox is not active: {current.state.value}"
            )

        raw = str(relative_path)
        if "\x00" in raw:
            raise SandboxPathError("NUL is not allowed in sandbox paths")
        if not raw:
            raise SandboxPathError("sandbox path must be non-empty")

        posix = PurePosixPath(raw)
        windows = PureWindowsPath(raw)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.drive)
            or ".." in posix.parts
            or ".." in windows.parts
        ):
            raise SandboxPathError("sandbox path must remain lease-relative")

        root = current.root.resolve(strict=False)
        self._assert_owned_root(current, root)
        candidate = (root / Path(raw)).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise SandboxPathError("sandbox path escapes lease root") from exc
        return candidate

    def _assert_owned_root(self, lease: SandboxLease, resolved_root: Path) -> None:
        expected = self._base_root / lease.sandbox_id
        if lease.root != expected:
            raise SandboxPathError("sandbox root is not manager-owned")
        try:
            resolved_root.relative_to(self._base_root)
        except ValueError as exc:
            raise SandboxPathError(
                "sandbox root resolves outside manager base"
            ) from exc

    @_lifecycle_serialized
    def _remove_root(self, root: Path) -> None:
        # Every destructive entry point, including acquire rollback and
        # direct _remove_root callers, must enforce the same quarantine gate.
        self._require_native_root_empty(root)
        if not root.exists() and not root.is_symlink():
            return
        if root.is_symlink():
            root.unlink(missing_ok=True)
            return
        is_junction = getattr(root, "is_junction", None)
        if callable(is_junction) and is_junction():
            root.rmdir()
            return
        shutil.rmtree(root)


__all__ = [
    "SandboxError",
    "NativeTreeUnverifiedError",
    "SandboxLeaseNotFoundError",
    "SandboxLeaseStateError",
    "SandboxManager",
    "SandboxPathError",
]
