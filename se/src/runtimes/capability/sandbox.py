from __future__ import annotations

import shutil
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
        self._leases: dict[str, SandboxLease] = {}
        self._profiles: dict[str, SandboxProfile] = {}

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
        self._profiles[profile.profile_id] = profile
        self._leases[sandbox_id] = active
        return active

    def current(self, lease: SandboxLease) -> SandboxLease:
        stored = self._leases.get(lease.sandbox_id)
        if stored is None:
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

        self._remove_root(current.root)
        destroyed = current.model_copy(
            update={"state": SandboxLeaseState.DESTROYED}
        )
        self._leases[current.sandbox_id] = destroyed
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
