"""SBX3 B0 SE-private invocation/tool-call authority fence (NOT ACTIVATED).

This is a durable *negative* bridge to the proposed Tools #371 internal
run_preconfined/verify_receipt protocol. It has no production OS-broker issuer
or full network-NONE confinement, so ALL native commands are DENIED before
spawn, and ALL candidate completion receipts fail verification.

The one-time random capability is not an OS security boundary against a
same-privilege malicious host; a separately privileged issuer/broker is
mandatory before enabling production. Never serialize it to Agent or model.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn, Any

from .native_broker_b0 import (
    DurableAttemptLedger,
    SpawnIdentity,
)


class NativeAuthorityDeniedB0(RuntimeError):
    """No native production dispatch; outcome never becomes SUCCESS."""


@dataclass(frozen=True, slots=True)
class OpaqueSpawnAuthorityB0:
    """Bound diagnostic credential; NOT a broker-authenticated OS receipt."""

    owner_id: str
    execution_id: str
    tool_call_id: str
    invocation_id: str
    sandbox_id: str
    lease_generation: str
    deadline_unix: float
    _secret: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        keys = (
            self.owner_id, self.execution_id, self.tool_call_id,
            self.invocation_id, self.sandbox_id, self.lease_generation,
        )
        if any(not isinstance(x, str) or not x or x != x.strip() for x in keys):
            raise ValueError("incomplete or noncanonical native authority identity")
        if not isinstance(self._secret, bytes) or len(self._secret) != 32:
            raise ValueError("invalid private authority credential")
        if (
            type(self.deadline_unix) not in (int, float)
            or not 0 < self.deadline_unix < 4102444800
        ):
            raise ValueError("invalid absolute native authority deadline")

    @property
    def identity(self) -> SpawnIdentity:
        return SpawnIdentity(
            self.owner_id, self.execution_id, self.tool_call_id,
            self.sandbox_id, self.lease_generation,
        )


class DurableNativeInvocationAuthorityB0:
    """One physical SQLite transaction owns authority + effect reservation.

    The pre-existing DurableAttemptLedger is intentionally reused in the SAME
    database; an authority cannot be issued without its no-replay reservation.
    This module never issues a production success or cleanup receipt.
    """

    def __init__(self, journal_path: str | Path) -> None:
        self.attempts = DurableAttemptLedger(journal_path)
        self.path = self.attempts.path
        with closing(self.attempts._connect()) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS native_invocation_authority_b0 (
                    execution_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    sandbox_id TEXT NOT NULL,
                    lease_generation TEXT NOT NULL,
                    deadline_unix REAL NOT NULL,
                    route TEXT NOT NULL CHECK(route='terminal.run'),
                    credential_hash BLOB NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                      ('ISSUED','PREFLIGHT_DENIED','OUTCOME_UNKNOWN')),
                    PRIMARY KEY (execution_id, tool_call_id)
                )"""
            )

    def mint_diagnostic(
        self, identity: SpawnIdentity, *, invocation_id: str,
        deadline_unix: float,
    ) -> OpaqueSpawnAuthorityB0:
        """Server-only diagnostic mint, transactionally paired with no-replay.

        Never call from model JSON/Tool schema; caller attestation still missing.
        """
        if not isinstance(invocation_id, str) or not invocation_id or invocation_id.strip() != invocation_id:
            raise NativeAuthorityDeniedB0("invalid invocation identity")
        if (
            type(deadline_unix) not in (float, int)
            or not time.time() < deadline_unix <= time.time() + 300
        ):
            raise NativeAuthorityDeniedB0("authority deadline must be bounded")
        secret = secrets.token_bytes(32)
        digest = hashlib.sha256(secret).digest()
        with closing(self.attempts._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO attempts VALUES (?, ?, ?, ?, ?, 'RESERVED')""",
                    (
                        identity.execution_id, identity.tool_call_id,
                        identity.owner_id, identity.sandbox_id,
                        identity.lease_generation,
                    ),
                )
                db.execute(
                    """INSERT INTO native_invocation_authority_b0
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'terminal.run', ?, 'ISSUED')""",
                    (
                        identity.execution_id, identity.tool_call_id,
                        invocation_id, identity.owner_id, identity.sandbox_id,
                        identity.lease_generation, deadline_unix, digest,
                    ),
                )
                db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise NativeAuthorityDeniedB0(
                    "native invocation is already reserved; replay denied"
                ) from exc
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return OpaqueSpawnAuthorityB0(
            identity.owner_id, identity.execution_id, identity.tool_call_id,
            invocation_id, identity.sandbox_id, identity.lease_generation,
            deadline_unix, secret,
        )

    def deny_before_spawn(self, authority: OpaqueSpawnAuthorityB0) -> NoReturn:
        """Atomically consume the diagnostic authority, with zero OS effects.

        No matter how convincing a caller's Python object/receipt looks, there
        is currently no trusted native OS isolation activation. Deny *before*
        subprocess creation and record the irreversible attempt disposition.
        """
        if not isinstance(authority, OpaqueSpawnAuthorityB0):
            raise NativeAuthorityDeniedB0("private SE authority is required")
        with closing(self.attempts._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    """SELECT owner_id, sandbox_id, lease_generation,
                              invocation_id, deadline_unix, credential_hash, state
                       FROM native_invocation_authority_b0
                       WHERE execution_id=? AND tool_call_id=?""",
                    (authority.execution_id, authority.tool_call_id),
                ).fetchone()
                if row is None:
                    raise NativeAuthorityDeniedB0("unregistered native authority")
                if (
                    row[0] != authority.owner_id
                    or row[1] != authority.sandbox_id
                    or row[2] != authority.lease_generation
                    or row[3] != authority.invocation_id
                    or row[4] != authority.deadline_unix
                    or row[6] != "ISSUED"
                    or not secrets.compare_digest(
                        row[5], hashlib.sha256(authority._secret).digest()
                    )
                ):
                    raise NativeAuthorityDeniedB0(
                        "foreign, forged, consumed or stale authority"
                    )
                # Expired or unexpired, this diagnostic stage always burns
                # the capability before ANY possible OS spawn. Preserve the
                # expiry reason after the durable transaction, never permit a
                # future caller to reinterpret an expired token as valid.
                expired = time.time() >= authority.deadline_unix
                changed = db.execute(
                    """UPDATE native_invocation_authority_b0
                       SET state='PREFLIGHT_DENIED'
                       WHERE execution_id=? AND tool_call_id=? AND state='ISSUED'""",
                    (authority.execution_id, authority.tool_call_id),
                ).rowcount
                attempt = db.execute(
                    """UPDATE attempts SET state='DENIED'
                       WHERE execution_id=? AND tool_call_id=? AND
                             owner_id=? AND sandbox_id=? AND
                             lease_generation=? AND state='RESERVED'""",
                    (
                        authority.execution_id, authority.tool_call_id,
                        authority.owner_id, authority.sandbox_id,
                        authority.lease_generation,
                    ),
                ).rowcount
                if changed != 1 or attempt != 1:
                    raise NativeAuthorityDeniedB0(
                        "durable reservation no longer permits this invocation"
                    )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        if expired:
            raise NativeAuthorityDeniedB0(
                "expired native authority irrevocably denied before OS spawn"
            )
        raise NativeAuthorityDeniedB0(
            "native SE B0 not production released: denied before OS spawn"
        )

    def revoke_generation(
        self, *, owner_id: str, execution_id: str,
        sandbox_id: str, lease_generation: str,
    ) -> int:
        """Server-only NEGATIVE revocation of exact generation, across workers.

        One SQLite BEGIN IMMEDIATE fence serializes generation revocation
        against the exact ticket preflight CAS and any other issuer. This
        method NEVER mints rights, launches a process or certifies cleanup.
        A hostile same-UID caller with direct database access remains an OS
        authority-isolation blocker; this is not a public endpoint.
        """
        scope = (owner_id, execution_id, sandbox_id, lease_generation)
        if any(
            not isinstance(v, str) or not v or v != v.strip()
            for v in scope
        ):
            raise NativeAuthorityDeniedB0("invalid generation revocation scope")
        with closing(self.attempts._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                rows = db.execute(
                    """SELECT tool_call_id FROM native_invocation_authority_b0
                       WHERE owner_id=? AND execution_id=? AND sandbox_id=?
                         AND lease_generation=? AND state='ISSUED'""",
                    scope,
                ).fetchall()
                for (tool_call_id,) in rows:
                    # A matching unresolved attempt MUST be retired in the
                    # same transaction as the authority state. If another
                    # transition occurred this invariant fails closed.
                    changed = db.execute(
                        """UPDATE attempts SET state='DENIED'
                           WHERE owner_id=? AND execution_id=? AND sandbox_id=?
                             AND lease_generation=? AND tool_call_id=?
                             AND state='RESERVED'""",
                        (*scope, tool_call_id),
                    ).rowcount
                    if changed != 1:
                        raise NativeAuthorityDeniedB0(
                            "native generation attempt state is inconsistent"
                        )
                changed = db.execute(
                    """UPDATE native_invocation_authority_b0
                       SET state='PREFLIGHT_DENIED'
                       WHERE owner_id=? AND execution_id=? AND sandbox_id=?
                         AND lease_generation=? AND state='ISSUED'""",
                    scope,
                ).rowcount
                if changed != len(rows):
                    raise NativeAuthorityDeniedB0(
                        "native generation revocation CAS was not exclusive"
                    )
                db.execute("COMMIT")
                return changed
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def reconcile_after_restart(self) -> int:
        """Conservatively invalidate minted-but-unconsumed credentials."""
        with closing(self.attempts._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                changed = db.execute(
                    """UPDATE native_invocation_authority_b0
                       SET state='OUTCOME_UNKNOWN' WHERE state='ISSUED'"""
                ).rowcount
                db.execute(
                    """UPDATE attempts SET state='OUTCOME_UNKNOWN'
                       WHERE state IN ('RESERVED','DISPATCHING')"""
                )
                db.execute("COMMIT")
                return changed
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def state(self, identity: SpawnIdentity) -> tuple[str, str] | None:
        with closing(self.attempts._connect()) as db:
            row = db.execute(
                """SELECT a.state, n.state FROM attempts a
                   JOIN native_invocation_authority_b0 n
                     ON n.execution_id=a.execution_id
                    AND n.tool_call_id=a.tool_call_id
                   WHERE a.execution_id=? AND a.tool_call_id=?
                     AND a.owner_id=? AND a.sandbox_id=?
                     AND a.lease_generation=? AND n.owner_id=a.owner_id
                     AND n.sandbox_id=a.sandbox_id
                     AND n.lease_generation=a.lease_generation""",
                (
                    identity.execution_id, identity.tool_call_id,
                    identity.owner_id, identity.sandbox_id,
                    identity.lease_generation,
                ),
            ).fetchone()
        return (row[0], row[1]) if row is not None else None


class NativeTerminalB0FailClosedPort:
    """Shape-compatible #371 private spawn_port; never dispatches or certifies."""

    def __init__(self, journal: DurableNativeInvocationAuthorityB0) -> None:
        self.journal = journal

    async def run_preconfined(
        self, *, authority: OpaqueSpawnAuthorityB0, command: str,
        timeout: int, sandbox_relative_cwd: str | None, encoding: str,
        max_stdout_bytes: int, max_stderr_bytes: int, max_total_output_bytes: int,
    ) -> Any:
        # The broker must validate route, network NONE, OS generation, profile,
        # syscall confinement and stdout limits BEFORE enabling real commands.
        # Until then do not reach subprocess.Popen or produce any completion.
        self.journal.deny_before_spawn(authority)

    async def verify_receipt(
        self, *, authority: OpaqueSpawnAuthorityB0, completion: Any,
    ) -> bool:
        # User-constructed success/VERIFIED_EMPTY objects or a Python boolean
        # do NOT establish OS-tree custody or generation-specific completion.
        return False


__all__ = [
    "DurableNativeInvocationAuthorityB0",
    "NativeAuthorityDeniedB0",
    "NativeTerminalB0FailClosedPort",
    "OpaqueSpawnAuthorityB0",
]
