"""SBX-3 B0 experimental native broker seam; deliberately NOT production-wired.

This is a staged *security-negative* and Linux namespace diagnostic, not a
TerminalTool driver. It never certifies VERIFIED_EMPTY or persists Tool SUCCESS.
Unfinished/crashed attempts become OUTCOME_UNKNOWN, permanently non-replayable.
Windows AppContainer/LPAC is not yet implemented: pre-spawn DENY is mandatory.
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


class B0ReplayDenied(RuntimeError):
    """A durable effect attempt already exists; never dispatch twice."""


class B0LedgerConflict(RuntimeError):
    """A compare-and-swap journal transition was rejected."""


@dataclass(frozen=True)
class SpawnIdentity:
    owner_id: str
    execution_id: str
    tool_call_id: str
    sandbox_id: str
    lease_generation: str

    def __post_init__(self) -> None:
        fields = (
            self.owner_id,
            self.execution_id,
            self.tool_call_id,
            self.sandbox_id,
            self.lease_generation,
        )
        if any(not isinstance(v, str) or not v or v != v.strip() for v in fields):
            raise ValueError("all broker identity fields must be canonical")


@dataclass(frozen=True)
class ProbeObservation:
    # Not a CapabilityInvocation or AgentToolResult and NEVER an attestation.
    disposition: str
    returncode: int | None
    stdout: str
    stderr: str
    native_launch_attempted: bool
    certified: bool = False


class DurableAttemptLedger:
    """SQLite FULL-synchronous, cross-process UNIQUE and CAS B0 effect journal.

    The ledger must live outside the sandbox root and must be retained across
    worker restart. The caller is trusted server code, never model input.
    Replaying an existing tool_call is denied even after DENIED or UNKNOWN.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("broker database cannot be a symbolic link")
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS attempts (
                    execution_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    lease_generation TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                      ('RESERVED', 'DISPATCHING', 'DENIED', 'OUTCOME_UNKNOWN')),
                    PRIMARY KEY (execution_id, tool_call_id)
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def reserve(self, identity: SpawnIdentity) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO attempts VALUES (?, ?, ?, ?, ?, 'RESERVED')""",
                    (
                        identity.execution_id,
                        identity.tool_call_id,
                        identity.owner_id,
                        identity.sandbox_id,
                        identity.lease_generation,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise B0ReplayDenied(
                    "tool call has a previous durable reservation; never replay"
                ) from exc
            db.execute("COMMIT")

    def transition(self, identity: SpawnIdentity, old: str, new: str) -> None:
        permitted = {
            ("RESERVED", "DISPATCHING"),
            ("RESERVED", "DENIED"),
            ("RESERVED", "OUTCOME_UNKNOWN"),
            ("DISPATCHING", "OUTCOME_UNKNOWN"),
        }
        if (old, new) not in permitted:
            raise B0LedgerConflict("unapproved effect journal transition")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """UPDATE attempts SET state=?
                   WHERE execution_id=? AND tool_call_id=? AND owner_id=?
                     AND sandbox_id=? AND lease_generation=? AND state=?""",
                (
                    new,
                    identity.execution_id,
                    identity.tool_call_id,
                    identity.owner_id,
                    identity.sandbox_id,
                    identity.lease_generation,
                    old,
                ),
            )
            if cursor.rowcount != 1:
                db.execute("ROLLBACK")
                raise B0LedgerConflict("stale, foreign or already fenced attempt")
            db.execute("COMMIT")

    def reconcile_after_restart(self) -> int:
        """Unknown even for RESERVED: dispatch-vs-crash order is unprovable."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """UPDATE attempts SET state='OUTCOME_UNKNOWN'
                   WHERE state IN ('RESERVED', 'DISPATCHING')"""
            ).rowcount
            db.execute("COMMIT")
            return changed

    def state(self, identity: SpawnIdentity) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT state FROM attempts
                   WHERE execution_id=? AND tool_call_id=? AND owner_id=?
                     AND sandbox_id=? AND lease_generation=?""",
                (
                    identity.execution_id,
                    identity.tool_call_id,
                    identity.owner_id,
                    identity.sandbox_id,
                    identity.lease_generation,
                ),
            ).fetchone()
        return str(row[0]) if row is not None else None


class NativeBrokerB0Diagnostic:
    """Linux bubblewrap diagnostic only: never connected to Agent dispatch.

    No readiness flag or receipt is issued, even when a Linux probe exits zero.
    A future production broker needs durable OS-tree reattachment/verification,
    a root-generation fence, and real native Windows network-NONE enforcement.
    """

    def __init__(self, ledger: DurableAttemptLedger) -> None:
        self.ledger = ledger

    @staticmethod
    def _linux_bwrap() -> str | None:
        if platform.system() != "Linux":
            return None
        discovered = shutil.which("bwrap")
        if not discovered:
            return None
        path = Path(discovered).resolve()
        try:
            mode = path.stat()
        except OSError:
            return None
        # Only a trusted system-controlled executable; never a repo-local shim.
        if not path.is_absolute() or mode.st_uid != 0:
            return None
        if mode.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            return None
        if not (mode.st_mode & stat.S_IXOTH):
            return None
        return str(path)

    @staticmethod
    def _linux_argv(binary: str, sandbox_root: Path, script: str) -> list[str]:
        args = [
            binary,
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--setenv", "HOME", "/work",
            "--setenv", "PATH", "/usr/bin",
            "--ro-bind", "/usr", "/usr",
        ]
        for folder in ("/lib", "/lib64"):
            if Path(folder).exists() and not Path(folder).is_symlink():
                args.extend(("--ro-bind", folder, folder))
        args.extend(
            (
                "--proc", "/proc",
                "--dev", "/dev",
                "--tmpfs", "/tmp",
                "--dir", "/work",
                "--bind", str(sandbox_root), "/work",
                "--chdir", "/work",
                "--",
                "/usr/bin/python3", "-c", script,
            )
        )
        return args

    def probe_python(
        self,
        identity: SpawnIdentity,
        sandbox_root: Path | str,
        script: str,
        *,
        timeout_seconds: float = 12.0,
    ) -> ProbeObservation:
        """Run ONE opt-in trusted diagnostic; never return Agent success.

        The diagnostic script is supplied by an operator/test, not an Agent,
        tool schema, client, chat, or model. A sandbox child may have had side
        effects before any error: treat even clean exit as OUTCOME_UNKNOWN.
        """
        if not isinstance(script, str) or not script or len(script) > 4096:
            raise ValueError("bounded operator diagnostic required")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("invalid bounded probe timeout")
        self.ledger.reserve(identity)  # Durable before any possible effect.

        root = Path(sandbox_root).absolute()
        binary = self._linux_bwrap()
        if (
            binary is None
            or not root.is_dir()
            or root.is_symlink()
            or root == Path.home()
            or root == Path.cwd()
            or root == self.ledger.path.parent
            or self.ledger.path.is_relative_to(root)
        ):
            self.ledger.transition(identity, "RESERVED", "DENIED")
            return ProbeObservation(
                disposition="DENIED_UNSUPPORTED_PRESPAWN",
                returncode=None,
                stdout="",
                stderr="",
                native_launch_attempted=False,
            )

        args = self._linux_argv(binary, root, script)
        self.ledger.transition(identity, "RESERVED", "DISPATCHING")
        # If the worker disappears now, recovery converts DISPATCHING to
        # non-retryable OUTCOME_UNKNOWN. bwrap --die-with-parent is not an
        # authoritative recovery receipt or replacement for cgroup tree proof.
        try:
            completed = subprocess.run(
                args,
                shell=False,
                cwd=str(root),
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            rc = completed.returncode
            stdout = completed.stdout[:4096]
            stderr = completed.stderr[:4096]
        except (OSError, subprocess.TimeoutExpired) as exc:
            rc, stdout, stderr = None, "", type(exc).__name__
        self.ledger.transition(identity, "DISPATCHING", "OUTCOME_UNKNOWN")
        return ProbeObservation(
            disposition="OUTCOME_UNKNOWN_NO_DURABLE_TREE_PROOF",
            returncode=rc,
            stdout=stdout,
            stderr=stderr,
            native_launch_attempted=True,
        )


__all__ = [
    "B0LedgerConflict",
    "B0ReplayDenied",
    "DurableAttemptLedger",
    "NativeBrokerB0Diagnostic",
    "ProbeObservation",
    "SpawnIdentity",
]
