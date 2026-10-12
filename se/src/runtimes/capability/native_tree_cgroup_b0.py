"""SBX-3 B0 experimental Linux cgroup v2 durable tree recovery.

The kernel owns the process membership, not a worker PID list. A new worker
can reattach to an immutable cgroup device/inode and kill/check *all* descendant
processes. This diagnostic is NOT wired to Agent or SandboxManager.destroy.
There is intentionally no method to turn a diagnostic receipt into SUCCESS
or remove a sandbox root: atomic trusted spawn/finalization is not yet built.
"""

from __future__ import annotations

import ctypes
import os
import platform
import sqlite3
import stat
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

_CGROUP2_SUPER_MAGIC = 0x63677270


class CgroupTreeUnavailable(RuntimeError):
    """Host lacks a verified delegated cgroup v2 kernel control directory."""


class CgroupTreeMismatch(RuntimeError):
    """Durable generation identity and live OS-owned tree no longer agree."""


class CgroupTreeNotEmpty(RuntimeError):
    """The OS has not proved all descendants are gone."""


@dataclass(frozen=True)
class CgroupTreeIdentity:
    owner_id: str
    execution_id: str
    sandbox_id: str
    generation: str

    def __post_init__(self) -> None:
        parts = (self.owner_id, self.execution_id, self.sandbox_id)
        if any(not isinstance(x, str) or not x or x.strip() != x for x in parts):
            raise ValueError("noncanonical owner, execution or sandbox identity")
        try:
            if uuid.UUID(self.generation).hex != self.generation.replace("-", ""):
                raise ValueError("noncanonical tree generation")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid tree generation") from exc


@dataclass(frozen=True)
class VerifiedEmptyWitness:
    """Stage B0 observation only; not an Agent completion or cleanup authority."""

    owner_id: str
    execution_id: str
    sandbox_id: str
    generation: str
    cgroup_device: int
    cgroup_inode: int
    status: str = "VERIFIED_EMPTY"
    production_usable: bool = False


class LinuxCgroupTreeLedger:
    """Durable OS-owned tree registry with an exact-generation kernel witness.

    Requires a pre-delegated cgroup2 *parent* with private write authority.
    No procfs PID enumeration, inferred process exit, synthetic events, or
    marker deletion may mint VERIFIED_EMPTY.
    """

    def __init__(self, delegate_root: Path | str, journal_path: Path | str) -> None:
        if platform.system() != "Linux":
            raise CgroupTreeUnavailable("Linux cgroup v2 not available on this OS")
        supplied = Path(delegate_root)
        if not supplied.is_absolute() or supplied.is_symlink():
            raise CgroupTreeUnavailable("delegated root must be absolute, not symlink")
        self.root = supplied.resolve(strict=True)
        self._assert_real_cgroup2(self.root)
        if not self.root.is_dir():
            raise CgroupTreeUnavailable("cgroup v2 parent is not a directory")
        rstat = self.root.stat()
        self._root_device_inode = (rstat.st_dev, rstat.st_ino)
        self.path = Path(journal_path).absolute()
        if self.path.is_symlink() or self.path.is_relative_to(self.root):
            raise CgroupTreeUnavailable("durable journal must be outside kernel cgroup mount")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            self._enable_wal(db)
            db.execute(
                """CREATE TABLE IF NOT EXISTS native_cgroup_trees (
                    sandbox_id TEXT NOT NULL PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    generation TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    device INTEGER NOT NULL,
                    inode INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('ENROLLED', 'VERIFIED_EMPTY'))
                )"""
            )

    @staticmethod
    def _assert_real_cgroup2(path: Path) -> None:
        # Linux statfs(2) f_type is the kernel filesystem magic. A tmpfs
        # directory containing forged cgroup.events/cgroup.kill must FAIL.
        libc = ctypes.CDLL(None, use_errno=True)
        buf = ctypes.create_string_buffer(256)
        if libc.statfs(ctypes.c_char_p(os.fsencode(path)), ctypes.byref(buf)) != 0:
            raise CgroupTreeUnavailable("cannot verify cgroup2 kernel superblock")
        value = ctypes.c_long.from_buffer(buf).value
        if value != _CGROUP2_SUPER_MAGIC:
            raise CgroupTreeUnavailable("not an actual Linux cgroup v2 filesystem")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=15000")
            db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            db.close()
            raise
        return db

    @staticmethod
    def _enable_wal(db: sqlite3.Connection) -> None:
        for attempt in range(10):
            try:
                row = db.execute("PRAGMA journal_mode=WAL").fetchone()
                if row is None or row[0].lower() != "wal":
                    raise CgroupTreeUnavailable("durable WAL not active")
                return
            except sqlite3.OperationalError as exc:
                if ("locked" not in str(exc).lower()
                        and "busy" not in str(exc).lower()):
                    raise
                if attempt == 9:
                    raise CgroupTreeUnavailable("durable WAL bootstrap busy") from exc
                time.sleep(.02 * (attempt + 1))

    def _assert_parent(self) -> None:
        self._assert_real_cgroup2(self.root)
        current = self.root.stat()
        if (current.st_dev, current.st_ino) != self._root_device_inode:
            raise CgroupTreeMismatch("delegated parent inode changed")
        if self.root.is_symlink() or not self.root.is_dir():
            raise CgroupTreeMismatch("delegated parent replaced")

    def enroll(self, identity: CgroupTreeIdentity) -> Path:
        """Persist identity before any trusted test worker enters the cgroup.

        Any crash between mkdir and durable INSERT leaves an orphan that MUST
        be treated as unknown by an administrative reconciliation procedure.
        """
        self._assert_parent()
        name = "sbx3-" + identity.generation.replace("-", "")
        path = self.root / name
        if path.exists() or path.is_symlink():
            raise CgroupTreeMismatch("cgroup name reused or already exists")
        # Never adopt a pre-existing cgroup with an assumed generation.
        try:
            path.mkdir(mode=0o700)
        except OSError as exc:
            raise CgroupTreeUnavailable("cannot create delegated cgroup") from exc
        pstat = path.stat()
        if not stat.S_ISDIR(pstat.st_mode):
            raise CgroupTreeMismatch("unexpected kernel cgroup type")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO native_cgroup_trees
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'ENROLLED')""",
                    (
                        identity.sandbox_id,
                        identity.owner_id,
                        identity.execution_id,
                        identity.generation,
                        str(path),
                        pstat.st_dev,
                        pstat.st_ino,
                    ),
                )
            except BaseException:
                db.execute("ROLLBACK")
                # Keep orphan cgroup for human/kernel forensic recovery.
                raise
            db.execute("COMMIT")
        return path

    def discover_unregistered_cgroups(self) -> tuple[Path, ...]:
        """Fail-closed orphan discovery after mkdir-before-INSERT hard crash.

        Returns only kernel cgroup directories without a durable record. It
        NEVER attaches, kills, removes, or certifies these unknown generations.
        """
        self._assert_parent()
        with closing(self._connect()) as db:
            known = {
                row[0] for row in db.execute(
                    "SELECT path FROM native_cgroup_trees"
                ).fetchall()
            }
        unknown = []
        for child in self.root.iterdir():
            if not child.name.startswith("sbx3-"):
                continue
            if child.is_symlink() or not child.is_dir():
                raise CgroupTreeMismatch("unknown cgroup entry is not a kernel directory")
            self._assert_real_cgroup2(child)
            if str(child) not in known:
                unknown.append(child)
        return tuple(sorted(unknown))

    def _load(self, identity: CgroupTreeIdentity) -> tuple[Path, int, int, str]:
        self._assert_parent()
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT path, device, inode, state FROM native_cgroup_trees
                   WHERE sandbox_id=? AND owner_id=? AND execution_id=?
                     AND generation=?""",
                (
                    identity.sandbox_id, identity.owner_id,
                    identity.execution_id, identity.generation,
                ),
            ).fetchone()
        if row is None:
            raise CgroupTreeMismatch("foreign, missing or stale generation")
        path = Path(row[0])
        if path.parent != self.root or path.name != "sbx3-" + identity.generation.replace("-", ""):
            raise CgroupTreeMismatch("durable cgroup path is not canonical")
        if path.is_symlink() or not path.is_dir():
            raise CgroupTreeMismatch("OS-owned cgroup disappeared or was redirected")
        self._assert_real_cgroup2(path)
        info = path.stat()
        if (info.st_dev, info.st_ino) != (row[1], row[2]):
            raise CgroupTreeMismatch("cgroup inode substituted across restart")
        for required in ("cgroup.procs", "cgroup.events", "cgroup.kill"):
            if not (path / required).is_file():
                raise CgroupTreeUnavailable("OS-owned control file absent: " + required)
        return path, int(row[1]), int(row[2]), str(row[3])

    def recover_and_verify_empty(
        self, identity: CgroupTreeIdentity, *, timeout_seconds: float = 10.0
    ) -> VerifiedEmptyWitness:
        """Kernel reattach, kill entire hierarchy, observe populated=0.

        cgroup.kill operates on all descendants; cgroup.events populated=0
        covers both direct and nested descendants. This is not a PID-exit or
        ps/proc polling shortcut. A timeout or uncertainty leaves ENROLLED.
        """
        if not 0 < timeout_seconds <= 30:
            raise ValueError("invalid kernel recovery timeout")
        path, device, inode, status = self._load(identity)
        if status != "ENROLLED":
            raise CgroupTreeMismatch("generation already finalized; no replay")
        try:
            (path / "cgroup.kill").write_text("1", encoding="ascii")
        except OSError as exc:
            raise CgroupTreeUnavailable("kernel hierarchy kill unsupported") from exc
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                events = dict(
                    line.split(maxsplit=1)
                    for line in (path / "cgroup.events").read_text("ascii").splitlines()
                )
                populated = events.get("populated")
                pids = (path / "cgroup.procs").read_text("ascii").strip()
            except (OSError, ValueError) as exc:
                raise CgroupTreeUnavailable("kernel tree state unreadable") from exc
            if populated == "0" and not pids:
                # Ensure neither the cgroup mount nor leaf identity changed
                # between the first observation and durable attestation.
                _path, _dev, _ino, still = self._load(identity)
                if (_path, _dev, _ino, still) != (path, device, inode, "ENROLLED"):
                    raise CgroupTreeMismatch("live tree changed during verification")
                # Recheck, never trust a single sample or a fabricated file.
                if "populated 0" not in (path / "cgroup.events").read_text("ascii").splitlines():
                    raise CgroupTreeNotEmpty("kernel tree became populated again")
                with closing(self._connect()) as db:
                    db.execute("BEGIN IMMEDIATE")
                    changed = db.execute(
                        """UPDATE native_cgroup_trees SET state='VERIFIED_EMPTY'
                           WHERE sandbox_id=? AND owner_id=? AND execution_id=?
                           AND generation=? AND device=? AND inode=? AND state='ENROLLED'""",
                        (
                            identity.sandbox_id, identity.owner_id,
                            identity.execution_id, identity.generation,
                            device, inode,
                        ),
                    ).rowcount
                    if changed != 1:
                        db.execute("ROLLBACK")
                        raise CgroupTreeMismatch("generation CAS changed before receipt")
                    db.execute("COMMIT")
                return VerifiedEmptyWitness(
                    identity.owner_id, identity.execution_id,
                    identity.sandbox_id, identity.generation, device, inode,
                )
            time.sleep(.05)
        raise CgroupTreeNotEmpty("OS-owned process hierarchy did not become empty")


__all__ = [
    "CgroupTreeIdentity",
    "CgroupTreeMismatch",
    "CgroupTreeNotEmpty",
    "CgroupTreeUnavailable",
    "LinuxCgroupTreeLedger",
    "VerifiedEmptyWitness",
]
