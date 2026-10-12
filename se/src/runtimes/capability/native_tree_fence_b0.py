"""Experimental cross-process native tree spawn/cleanup ordering fence (Linux).

Transactions in the *same* durable cgroup journal serialize trusted spawn
admission against closing. The process must be OS-stopped before admission:
attach to its exact generation cgroup before any untrusted instruction resumes.
This is NOT a production spawn broker: external host processes can still evade
the cooperative protocol; Windows native equivalent and root release absent.
"""

from __future__ import annotations

import os
import platform
import signal
import sqlite3
from contextlib import contextmanager, closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from .native_tree_cgroup_b0 import CgroupTreeIdentity, LinuxCgroupTreeLedger


class NativeSpawnFenceDenied(RuntimeError):
    """Native dispatch is no longer authorized for this OS-tree generation."""


@dataclass
class _StoppedSpawnAdmission:
    """Owner-held SQL write lock; not user- or Agent-callable authority."""

    ledger: "LinuxCgroupTreeLedger"
    identity: "CgroupTreeIdentity"
    os_cgroup: object
    active: bool = True
    admitted: bool = False

    def admit_stopped_process(self, pid: int) -> None:
        """Attach a *trusted pre-exec stopped* child before SIGCONT.

        The caller creates a trusted trampoline that SIGSTOPs before executing
        any untrusted code. This method checks a kernel stop state, pins a pidfd
        while moving the PID, then resumes only after OS cgroup membership is
        observed. Caller MUST keep the SQL gate open until this method returns.
        """
        if not self.active or self.admitted:
            raise NativeSpawnFenceDenied("spawn admission expired or already used")
        if platform.system() != "Linux" or not isinstance(pid, int) or pid <= 1:
            raise NativeSpawnFenceDenied("Linux stopped child PID required")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise NativeSpawnFenceDenied("kernel pidfd signal identity unavailable")
        try:
            pidfd = os.pidfd_open(pid, 0)
        except OSError as exc:
            raise NativeSpawnFenceDenied("cannot pin stopped child identity") from exc
        try:
            self._require_stopped(pid)
            try:
                target = self.os_cgroup / "cgroup.procs"
                target.write_text(str(pid), encoding="ascii")
                # Observe membership *before* resuming this child. A cgroup
                # containing nested descendants may have an empty direct procs
                # file, but new admission is to the exact parent leaf.
                direct = target.read_text(encoding="ascii").split()
            except OSError as exc:
                raise NativeSpawnFenceDenied("kernel cgroup enrollment denied") from exc
            if str(pid) not in direct:
                raise NativeSpawnFenceDenied("kernel did not acknowledge process membership")
            self._require_stopped(pid)
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGCONT, None, 0)
            except OSError as exc:
                raise NativeSpawnFenceDenied("cannot resume pinned admitted child") from exc
            self.admitted = True
        finally:
            os.close(pidfd)

    @staticmethod
    def _require_stopped(pid: int) -> None:
        try:
            status = (os.path.join("/proc", str(pid), "status"))
            with open(status, encoding="ascii") as stream:
                state = next(
                    (line.split(":", 1)[1].strip()
                     for line in stream if line.startswith("State:")), ""
                )
        except OSError as exc:
            raise NativeSpawnFenceDenied("kernel process stop state unknown") from exc
        if not state or state[0] not in {"T", "t"}:
            raise NativeSpawnFenceDenied("process is not stopped before untrusted exec")


class NativeTreeLifecycleFence:
    """Cross-process DB transaction protocol sharing the kernel-tree ledger."""

    def __init__(self, ledger: "LinuxCgroupTreeLedger") -> None:
        self.ledger = ledger

    @staticmethod
    def _assert_fence_row(
        db: sqlite3.Connection, identity: "CgroupTreeIdentity",
        *, allowed: tuple[str, ...],
    ) -> str:
        row = db.execute(
            """SELECT phase FROM native_tree_lifecycle_fences
               WHERE sandbox_id=? AND owner_id=? AND execution_id=?
                 AND generation=?""",
            (
                identity.sandbox_id, identity.owner_id,
                identity.execution_id, identity.generation,
            ),
        ).fetchone()
        if row is None or row[0] not in allowed:
            raise NativeSpawnFenceDenied(
                "unregistered/stale generation or lifecycle transition fenced"
            )
        return str(row[0])

    @contextmanager
    def admit_spawn(
        self, identity: "CgroupTreeIdentity",
    ) -> Iterator[_StoppedSpawnAdmission]:
        """Hold SQLite BEGIN IMMEDIATE until stopped process is kernel-enrolled.

        A simultaneous cleanup cannot move OPEN->CLOSING while this window is
        open. After a crash SQLite rolls back/releases the writer lock; any
        already-enrolled child remains OS-owned for kernel kill on recovery.
        """
        with closing(self.ledger._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            gate = None
            try:
                self._assert_fence_row(db, identity, allowed=("OPEN",))
                path, _device, _inode, state = self.ledger._load(identity)
                if state != "ENROLLED":
                    raise NativeSpawnFenceDenied("native cgroup is already finalized")
                gate = _StoppedSpawnAdmission(self.ledger, identity, path)
                yield gate
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            finally:
                if gate is not None:
                    gate.active = False

    def begin_cleanup(self, identity: "CgroupTreeIdentity") -> None:
        """Commit permanent CLOSING before the first kernel cgroup.kill.

        A crash AFTER this commit retains the cross-process spawn denial.
        Restarted recovery may resume CLOSING without allowing new processes.
        """
        with closing(self.ledger._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._assert_fence_row(db, identity, allowed=("OPEN", "CLOSING"))
                row = db.execute(
                    """SELECT state FROM native_cgroup_trees
                       WHERE sandbox_id=? AND owner_id=? AND execution_id=?
                         AND generation=?""",
                    (
                        identity.sandbox_id, identity.owner_id,
                        identity.execution_id, identity.generation,
                    ),
                ).fetchone()
                if row is None or row[0] != "ENROLLED":
                    raise NativeSpawnFenceDenied("OS-tree ledger is not enrolled")
                db.execute(
                    """UPDATE native_tree_lifecycle_fences SET phase='CLOSING'
                       WHERE sandbox_id=? AND owner_id=? AND execution_id=?
                         AND generation=? AND phase='OPEN'""",
                    (
                        identity.sandbox_id, identity.owner_id,
                        identity.execution_id, identity.generation,
                    ),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def finish_cleanup(
        db: sqlite3.Connection, identity: "CgroupTreeIdentity",
    ) -> None:
        """Atomically finalize fence alongside the verified cgroup-row CAS."""
        NativeTreeLifecycleFence._assert_fence_row(
            db, identity, allowed=("CLOSING",),
        )
        changed = db.execute(
            """UPDATE native_tree_lifecycle_fences SET phase='VERIFIED_EMPTY'
               WHERE sandbox_id=? AND owner_id=? AND execution_id=?
                 AND generation=? AND phase='CLOSING'""",
            (
                identity.sandbox_id, identity.owner_id,
                identity.execution_id, identity.generation,
            ),
        ).rowcount
        if changed != 1:
            raise NativeSpawnFenceDenied("cleanup generation CAS did not succeed")


__all__ = [
    "NativeSpawnFenceDenied",
    "NativeTreeLifecycleFence",
]
