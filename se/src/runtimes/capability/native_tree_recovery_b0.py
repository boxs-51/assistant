"""SBX-3 B0 D2: fail-closed, durable Linux cgroup-v2 tree readback.

NOT wired to production spawn/Agent. No cgroup delegation or verified Windows
Job-Object recovery is assumed. A successful test of this module alone is NOT
B0 native certification. Only a trusted pre-exec launcher may bind a process
to an authenticated cgroup; this module cannot establish that prerequisite.

A VERIFIED_EMPTY proof always requires real Linux kernel cgroup-v2 state.
Never accept metadata, a PID, stale journal state or mock filesystem as proof.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .native_broker_b0 import SpawnIdentity


class NativeTreeHold(RuntimeError):
    """Unknown/foreign/unsupported native tree: no cleanup or durable success."""


AGENT_UNCERTIFIED_PROCESS_IDS = frozenset(
    {"terminal.run", "terminal.launch", "python.run"}
)


def require_no_uncertified_agent_success(
    capability_id: str, success: bool
) -> None:
    """D2 temporary global success fence, until broker readback is wired."""
    if success and capability_id in AGENT_UNCERTIFIED_PROCESS_IDS:
        raise NativeTreeHold(
            "SBX3_NATIVE_RECEIPT_UNAVAILABLE: no trusted native tree "
            "and result receipt; successful Agent process result is forbidden"
        )


@dataclass(frozen=True)
class NativeTreeRef:
    identity: SpawnIdentity
    cgroup_path: str
    cgroup_dev: int
    cgroup_ino: int


class LinuxCgroupTreeAuthority:
    """Persisted cgroup-v2 identity and reinspection under a trusted delegation.

    A cgroup-v2 path has to be set up by a privileged server-side launcher
    before first untrusted instruction. register() only records the known
    OS-owned cgroup and NEVER proves that a process was launched inside it.
    verify_empty() may attest an empty *registered* cgroup, but cannot certify
    launch-time confinement. Its return value is not an AgentToolResult.
    """

    def __init__(
        self,
        journal_path: Path | str,
        *,
        delegated_root: Path | str = "/sys/fs/cgroup",
        grace_seconds: float = 2.0,
    ) -> None:
        self.path = Path(journal_path).absolute()
        self.root = Path(delegated_root).resolve(strict=False)
        if not 0 < grace_seconds <= 30:
            raise ValueError("bounded grace required")
        self.grace_seconds = float(grace_seconds)
        if self.path.is_relative_to(self.root):
            raise ValueError("ledger must not live inside a cgroup filesystem")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise NativeTreeHold("journal symlink forbidden")
        with closing(self._db()) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS native_tree (
                    execution_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    lease_generation TEXT NOT NULL,
                    cgroup_path TEXT NOT NULL,
                    cgroup_dev INTEGER NOT NULL,
                    cgroup_ino INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('REGISTERED','FENCED','VERIFIED_EMPTY')
                    ),
                    PRIMARY KEY (execution_id,tool_call_id)
                )"""
            )

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), isolation_level=None, timeout=15)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def _assert_real_cgroup2(self, candidate: Path) -> tuple[int, int]:
        if platform.system() != "Linux":
            raise NativeTreeHold("unsupported platform: Windows native HOLD")
        try:
            canonical = candidate.resolve(strict=True)
            # Reject any lexical traversal or symlink indirection; the
            # stored OS path must be canonical in a trusted delegation.
            if candidate != canonical:
                raise NativeTreeHold("noncanonical/symlink cgroup ref")
            candidate.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise NativeTreeHold("cgroup outside trusted delegation") from exc
        if candidate == self.root:
            raise NativeTreeHold("refusing to own shared/root cgroup")
        # Parse the kernel mount table: a fabricated tmp_path/cgroup.events
        # must never become security evidence.
        mount_ok = False
        try:
            for line in Path("/proc/self/mountinfo").read_text().splitlines():
                before, after = line.split(" - ", 1)
                attrs = before.split()
                fs_fields = after.split()
                if len(attrs) > 4 and fs_fields[0] == "cgroup2":
                    kernel_mount = Path(attrs[4])
                    # Delegated subtrees can be BELOW the cgroup2 mount,
                    # but a fake directory on another filesystem cannot.
                    if self.root.is_relative_to(kernel_mount):
                        mount_ok = True
                        break
        except (OSError, ValueError, IndexError) as exc:
            raise NativeTreeHold("unreadable kernel cgroup mounts") from exc
        if not mount_ok:
            raise NativeTreeHold("not a verified cgroup-v2 mount")
        try:
            meta = candidate.stat()
            if not candidate.is_dir():
                raise NativeTreeHold("native tree is not a cgroup directory")
            if not (candidate / "cgroup.events").is_file():
                raise NativeTreeHold("kernel cgroup.events missing")
            return int(meta.st_dev), int(meta.st_ino)
        except OSError as exc:
            raise NativeTreeHold("unavailable cgroup scope") from exc

    def register(self, identity: SpawnIdentity, cgroup_path: Path | str) -> NativeTreeRef:
        try:
            path = Path(cgroup_path).resolve(strict=True)
        except OSError as exc:
            raise NativeTreeHold("native cgroup not found") from exc
        dev, ino = self._assert_real_cgroup2(path)
        if path.name != identity.sandbox_id:
            raise NativeTreeHold("cgroup name must bind sandbox id")
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO native_tree VALUES
                       (?,?,?,?,?,?,?,?,'REGISTERED')""",
                    (
                        identity.execution_id,
                        identity.tool_call_id,
                        identity.owner_id,
                        identity.sandbox_id,
                        identity.lease_generation,
                        str(path),
                        dev,
                        ino,
                    ),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise NativeTreeHold("duplicate tree binding/lease replay") from exc
        return NativeTreeRef(identity, str(path), dev, ino)

    def _get(self, identity: SpawnIdentity) -> tuple[NativeTreeRef, str]:
        with closing(self._db()) as db:
            row = db.execute(
                """SELECT owner_id,sandbox_id,lease_generation,cgroup_path,
                          cgroup_dev,cgroup_ino,state
                   FROM native_tree WHERE execution_id=? AND tool_call_id=?""",
                (identity.execution_id, identity.tool_call_id),
            ).fetchone()
        if row is None or tuple(row[:3]) != (
            identity.owner_id,
            identity.sandbox_id,
            identity.lease_generation,
        ):
            raise NativeTreeHold("missing/stale/foreign tree authority")
        ref = NativeTreeRef(identity, str(row[3]), int(row[4]), int(row[5]))
        if self._assert_real_cgroup2(Path(ref.cgroup_path)) != (
            ref.cgroup_dev,
            ref.cgroup_ino,
        ):
            raise NativeTreeHold("cgroup inode/generation substituted")
        return ref, str(row[6])

    def require_registered(self, identity: SpawnIdentity) -> None:
        """Check persisted OS binding before untrusted process spawn."""
        _, state = self._get(identity)
        if state != "REGISTERED":
            raise NativeTreeHold("native tree not fresh/registered")

    def require_known_tree(self, identity: SpawnIdentity) -> None:
        """Reattach a persisted OS-owned scope without granting any SUCCESS.

        Restarted managers must recover into CLEANUP/QUARANTINE, never ACTIVE.
        VERIFIED_EMPTY still requires separate fresh OS readback.
        """
        self._get(identity)

    @staticmethod
    def _read_kv(path: Path) -> dict[str, str]:
        try:
            return dict(line.split(maxsplit=1) for line in path.read_text().splitlines())
        except (OSError, ValueError) as exc:
            raise NativeTreeHold("kernel state unreadable") from exc

    def fence_and_verify_empty(self, identity: SpawnIdentity) -> None:
        """Fence then kill, verify kernel empty, persist attested state.

        Requires trusted caller-exclusive launch gate. A failed attempt remains
        FENCED; the root remains quarantined, not removable.
        """
        ref, state = self._get(identity)
        if state not in {"REGISTERED", "FENCED", "VERIFIED_EMPTY"}:
            raise NativeTreeHold("unknown tree state")
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE native_tree SET state='FENCED'
                   WHERE execution_id=? AND tool_call_id=?
                     AND owner_id=? AND sandbox_id=? AND lease_generation=?
                     AND cgroup_dev=? AND cgroup_ino=?
                     AND state IN ('REGISTERED','FENCED','VERIFIED_EMPTY')""",
                (
                    identity.execution_id, identity.tool_call_id,
                    identity.owner_id, identity.sandbox_id,
                    identity.lease_generation, ref.cgroup_dev, ref.cgroup_ino,
                ),
            )
            if changed.rowcount != 1:
                db.execute("ROLLBACK")
                raise NativeTreeHold("concurrent tree fence failed")
            db.execute("COMMIT")

        path = Path(ref.cgroup_path)
        try:
            (path / "cgroup.freeze").write_text("1")
            (path / "cgroup.kill").write_text("1")
        except OSError as exc:
            raise NativeTreeHold("cannot freeze and kill kernel cgroup") from exc

        deadline = time.monotonic() + self.grace_seconds
        while True:
            if self._assert_real_cgroup2(path) != (ref.cgroup_dev, ref.cgroup_ino):
                raise NativeTreeHold("cgroup scope changed during cleanup")
            events = self._read_kv(path / "cgroup.events")
            try:
                frozen = (path / "cgroup.freeze").read_text().strip()
                procs = (path / "cgroup.procs").read_text().strip()
            except OSError as exc:
                raise NativeTreeHold("cannot read kernel tree readback") from exc
            if (
                events.get("populated") == "0"
                and events.get("frozen") == "1"
                and frozen == "1"
                and not procs
            ):
                break
            if time.monotonic() >= deadline:
                raise NativeTreeHold("tree not VERIFIED_EMPTY within deadline")
            time.sleep(0.02)

        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """UPDATE native_tree SET state='VERIFIED_EMPTY'
                   WHERE execution_id=? AND tool_call_id=?
                     AND owner_id=? AND sandbox_id=? AND lease_generation=?
                     AND cgroup_dev=? AND cgroup_ino=? AND state='FENCED'""",
                (
                    identity.execution_id, identity.tool_call_id,
                    identity.owner_id, identity.sandbox_id,
                    identity.lease_generation, ref.cgroup_dev, ref.cgroup_ino,
                ),
            )
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise NativeTreeHold("tree verification race")
            db.execute("COMMIT")

    def require_verified_empty(self, identity: SpawnIdentity) -> None:
        """Always reattach to real kernel state; never trust a persisted string."""
        ref, state = self._get(identity)
        if state != "VERIFIED_EMPTY":
            raise NativeTreeHold("no verified empty tree")
        path = Path(ref.cgroup_path)
        events = self._read_kv(path / "cgroup.events")
        if events.get("populated") != "0" or events.get("frozen") != "1":
            raise NativeTreeHold("native descendants not empty/fenced")
        try:
            if (path / "cgroup.freeze").read_text().strip() != "1":
                raise NativeTreeHold("native spawn fence absent")
            if (path / "cgroup.procs").read_text().strip():
                raise NativeTreeHold("native processes remain")
        except OSError as exc:
            raise NativeTreeHold("native OS readback failed") from exc


__all__ = [
    "AGENT_UNCERTIFIED_PROCESS_IDS",
    "NativeTreeHold",
    "NativeTreeRef",
    "LinuxCgroupTreeAuthority",
    "require_no_uncertified_agent_success",
]
