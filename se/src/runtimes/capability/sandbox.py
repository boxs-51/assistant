from __future__ import annotations

import shutil
from threading import RLock
from typing import TYPE_CHECKING
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

if TYPE_CHECKING:
    from .native_broker_b0 import SpawnIdentity
    from .native_tree_recovery_b0 import LinuxCgroupTreeAuthority

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


class SandboxManager:
    """Own execution-scoped ephemeral roots without granting host-path authority."""

    def __init__(
        self,
        base_root: Path | str,
        *,
        native_tree_authority: "LinuxCgroupTreeAuthority | None" = None,
    ) -> None:
        raw_base = Path(base_root).expanduser()
        if not raw_base.is_absolute():
            raise ValueError("sandbox base_root must be an explicit absolute path")
        self._base_root = raw_base.resolve(strict=False)
        if self._base_root in {Path.cwd().resolve(), Path.home().resolve()}:
            raise ValueError("sandbox base_root cannot be ambient cwd or user home")
        self._base_root.mkdir(parents=True, exist_ok=True)
        self._leases: dict[str, SandboxLease] = {}
        self._profiles: dict[str, SandboxProfile] = {}
        self._execution_leases: dict[str, str] = {}
        # D2 staged opt-in only. Production native spawn remains DISABLED.
        # All legacy SBX-1/2 manager instances preserve their semantics.
        self._native_tree_authority = native_tree_authority
        self._native_tree_leases: dict[str, "SpawnIdentity"] = {}
        self._native_root_lock = RLock()

    def bind_native_tree(
        self, lease: SandboxLease, identity: "SpawnIdentity"
    ) -> None:
        """Trusted-only pre-spawn bind; never exposed as a model capability.

        Native process launch must not occur until this succeeds. Root cleanup
        subsequently fails closed unless the *real OS* reattachment attests a
        generation-specific fenced empty cgroup.
        """
        with self._native_root_lock:
            stored = self.current(lease)
            if stored.state is not SandboxLeaseState.ACTIVE:
                raise SandboxLeaseStateError("cannot bind non-active native lease")
            if (
                identity.sandbox_id != stored.sandbox_id
                or identity.owner_id != stored.owner_user_id
                or identity.execution_id != stored.execution_id
            ):
                raise SandboxLeaseStateError("native OS tree identity mismatch")
            if self._native_tree_authority is None:
                raise SandboxLeaseStateError("native OS tree authority unavailable")
            self._native_tree_authority.require_registered(identity)
            if stored.sandbox_id in self._native_tree_leases:
                raise SandboxLeaseStateError("native tree already bound")
            self._native_tree_leases[stored.sandbox_id] = identity

    def reattach_native_after_restart(
        self,
        lease: SandboxLease,
        profile: SandboxProfile,
        identity: "SpawnIdentity",
    ) -> SandboxLease:
        """Restore a trusted native lease ONLY in CLEANUP quarantine.

        The actual lease/profile snapshot must come from authenticated server
        persistence, never model metadata. A missing/stale kernel tree denies
        adoption; there is no automatic spawn or SUCCESS on reattachment.
        """
        with self._native_root_lock:
            if self._native_tree_authority is None:
                raise SandboxLeaseStateError("native OS authority unavailable")
            expected = self._base_root / lease.sandbox_id
            if (
                lease.root != expected
                or lease.root.is_symlink()
                or not lease.root.is_dir()
                or profile.profile_id != lease.profile_id
                or identity.sandbox_id != lease.sandbox_id
                or identity.owner_id != lease.owner_user_id
                or identity.execution_id != lease.execution_id
                or lease.sandbox_id in self._leases
                or lease.execution_id in self._execution_leases
            ):
                raise SandboxLeaseStateError("unsafe native recovery identity/root")
            # This method reopens the kernel cgroup and verifies journal
            # owner/generation/inode. No stale serialized receipt is trusted.
            try:
                self._native_tree_authority.require_known_tree(identity)
            except Exception as exc:
                raise SandboxLeaseStateError(
                    "OS tree cannot be reattached; root stays quarantined"
                ) from exc
            recovered = lease.model_copy(
                update={"state": SandboxLeaseState.CLEANUP}
            )
            self._profiles.setdefault(profile.profile_id, profile)
            self._leases[lease.sandbox_id] = recovered
            self._execution_leases[lease.execution_id] = lease.sandbox_id
            self._native_tree_leases[lease.sandbox_id] = identity
            return recovered

    @property
    def base_root(self) -> Path:
        return self._base_root

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

        # Root deletion must not precede a broker-authenticated, OS-backed
        # tree-empty readback. Unknown/stale/crashed OS state quarantines the
        # root in CLEANUP. The gate is inactive for legacy SBX-1/2 leases.
        with self._native_root_lock:
            native_identity = self._native_tree_leases.get(current.sandbox_id)
            if native_identity is not None:
                if self._native_tree_authority is None:
                    raise SandboxLeaseStateError("native OS authority unavailable")
                try:
                    self._native_tree_authority.require_verified_empty(
                        native_identity
                    )
                except Exception as exc:
                    raise SandboxLeaseStateError(
                        "native root deletion HOLD: VERIFIED_EMPTY unproven"
                    ) from exc
            self._remove_root(current.root)
            self._native_tree_leases.pop(current.sandbox_id, None)
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

    @staticmethod
    def _remove_root(root: Path) -> None:
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
    "SandboxLeaseNotFoundError",
    "SandboxLeaseStateError",
    "SandboxManager",
    "SandboxPathError",
]
