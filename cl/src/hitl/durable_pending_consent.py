"""E-P1B dormant client-side durable human-consent gate (NOT wired to UI).

This authority is deliberately opt-in. A crashed human PENDING decision never
silently becomes APPROVED, and a process that might have started an external
effect must never be replayed merely because the UI/client reconnected.
Integration with the actual UI/dispatcher needs a separate reviewed stage.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


class ConsentConflict(RuntimeError):
    """A caller changed the immutable invocation/identity/request binding."""


class ConsentNotRecoverable(RuntimeError):
    """A terminal or effect-started request cannot be re-prompted or replayed."""


@dataclass(frozen=True)
class ConsentRecord:
    client_id: str
    principal_id: str
    invocation_id: str
    capability_id: str
    request_fingerprint: str
    risk_level: str
    approval_id: str
    boot_generation: str
    state: str
    revision: int
    expires_at: float


_STATES = frozenset({
    "PENDING", "APPROVED", "DENIED", "EFFECT_STARTED", "EXPIRED",
})


class DurablePendingConsentStore:
    """SQLite write-serialized pre-effect consent state, never a UI authority.

    Client and principal are trusted values supplied by the authenticated
    dispatcher, NOT received from tool arguments or model-generated text.
    The approval ID is random and short-lived. An APPROVED decision is not a
    proof of OS-authenticated user identity; that remains the UI adapter's
    responsibility. All cross-process effects must call claim_effect() BEFORE
    any side effect. EFFECT_STARTED is permanently unknown after crash.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        boot_generation: str | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.boot_generation = boot_generation or secrets.token_hex(24)
        if not isinstance(self.boot_generation, str) or len(self.boot_generation) < 16:
            raise ValueError("Invalid boot generation")
        self._now = now
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS durable_hitl_pending (
                    client_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    risk_level TEXT NOT NULL CHECK (risk_level IN ('HIGH','CRITICAL')),
                    approval_id TEXT NOT NULL UNIQUE,
                    boot_generation TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('PENDING','APPROVED','DENIED',
                                  'EFFECT_STARTED','EXPIRED')
                    ),
                    revision INTEGER NOT NULL CHECK (revision >= 0),
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (client_id, principal_id, invocation_id)
                )
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=10.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _keys(client_id: str, principal_id: str, invocation_id: str,
              capability_id: str, request_fingerprint: str) -> tuple[str, ...]:
        parts = (client_id, principal_id, invocation_id,
                 capability_id, request_fingerprint)
        if not all(isinstance(item, str) and item.strip() for item in parts):
            raise ValueError("Authenticated nonempty consent binding required")
        return parts

    @staticmethod
    def _record(row: sqlite3.Row) -> ConsentRecord:
        return ConsentRecord(**dict(row))

    @staticmethod
    def _get(db: sqlite3.Connection, keys: tuple[str, ...]):
        return db.execute(
            "SELECT * FROM durable_hitl_pending "
            "WHERE client_id=? AND principal_id=? AND invocation_id=?",
            keys[:3],
        ).fetchone()

    @staticmethod
    def _match(row: sqlite3.Row, keys: tuple[str, ...], risk_level: str):
        if (
            tuple(row[k] for k in (
                "client_id", "principal_id", "invocation_id",
                "capability_id", "request_fingerprint",
            )) != keys or row["risk_level"] != risk_level
        ):
            raise ConsentConflict("Consent identity or request drift")

    @staticmethod
    def _validate_risk(risk_level: str) -> None:
        if risk_level not in {"HIGH", "CRITICAL"}:
            raise ValueError("Only HIGH/CRITICAL human consent belongs here")

    def open_pending(
        self, *, client_id: str, principal_id: str, invocation_id: str,
        capability_id: str, request_fingerprint: str,
        risk_level: str, ttl_seconds: float = 300,
    ) -> ConsentRecord:
        """Create once; never silently reuse a stale precrash approval ID."""
        keys = self._keys(client_id, principal_id, invocation_id,
                          capability_id, request_fingerprint)
        self._validate_risk(risk_level)
        if not 0 < float(ttl_seconds) <= 3600:
            raise ValueError("Invalid bounded consent expiry")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._get(db, keys)
            if old:
                self._match(old, keys, risk_level)
                if (old["boot_generation"] != self.boot_generation
                        or old["state"] != "PENDING"):
                    raise ConsentNotRecoverable(
                        "Existing durable consent requires explicit review"
                    )
                return self._record(old)
            db.execute(
                "INSERT INTO durable_hitl_pending "
                "(client_id,principal_id,invocation_id,capability_id,"
                " request_fingerprint,risk_level,approval_id,boot_generation,"
                " state,revision,expires_at) "
                "VALUES (?,?,?,?,?,?,?,?, 'PENDING', 0, ?)",
                (*keys, risk_level, secrets.token_urlsafe(32),
                 self.boot_generation, self._now() + float(ttl_seconds)),
            )
            return self._record(self._get(db, keys))

    def reissue_after_restart(
        self, *, client_id: str, principal_id: str, invocation_id: str,
        capability_id: str, request_fingerprint: str,
        risk_level: str, ttl_seconds: float = 300,
    ) -> ConsentRecord:
        """Require a fresh human decision. Old UI IDs are burnt permanently."""
        keys = self._keys(client_id, principal_id, invocation_id,
                          capability_id, request_fingerprint)
        self._validate_risk(risk_level)
        if not 0 < float(ttl_seconds) <= 3600:
            raise ValueError("Invalid bounded consent expiry")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._get(db, keys)
            if old is None:
                raise ConsentNotRecoverable("Missing original pending consent")
            self._match(old, keys, risk_level)
            if old["boot_generation"] == self.boot_generation:
                raise ConsentNotRecoverable("Not a distinct process generation")
            if old["state"] not in {"PENDING", "APPROVED"}:
                raise ConsentNotRecoverable(
                    "Denied, expired or potentially executed consent is immutable"
                )
            if old["expires_at"] <= self._now():
                db.execute(
                    "UPDATE durable_hitl_pending "
                    "SET state='EXPIRED', revision=revision+1 "
                    "WHERE client_id=? AND principal_id=? AND invocation_id=?",
                    keys[:3],
                )
                raise ConsentNotRecoverable("Original consent has expired")
            db.execute(
                "UPDATE durable_hitl_pending SET state='PENDING', "
                " approval_id=?, boot_generation=?, revision=revision+1, "
                " expires_at=? "
                "WHERE client_id=? AND principal_id=? AND invocation_id=?",
                (secrets.token_urlsafe(32), self.boot_generation,
                 min(old["expires_at"], self._now() + float(ttl_seconds)),
                 *keys[:3]),
            )
            return self._record(self._get(db, keys))

    def respond(
        self, *, client_id: str, principal_id: str, invocation_id: str,
        capability_id: str, request_fingerprint: str,
        approval_id: str, approve: bool,
    ) -> bool:
        """Return False on stale token, principal drift, replay or expiry."""
        keys = self._keys(client_id, principal_id, invocation_id,
                          capability_id, request_fingerprint)
        if type(approve) is not bool or not isinstance(approval_id, str):
            return False
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._get(db, keys)
            if old is None or old["capability_id"] != capability_id or (
                    old["request_fingerprint"] != request_fingerprint):
                return False
            if (old["boot_generation"] != self.boot_generation
                    or old["approval_id"] != approval_id
                    or old["state"] != "PENDING"):
                return False
            if old["expires_at"] <= self._now():
                db.execute(
                    "UPDATE durable_hitl_pending "
                    "SET state='EXPIRED', revision=revision+1 "
                    "WHERE client_id=? AND principal_id=? AND invocation_id=?",
                    keys[:3],
                )
                return False
            cursor = db.execute(
                "UPDATE durable_hitl_pending "
                "SET state=?, revision=revision+1 "
                "WHERE client_id=? AND principal_id=? AND invocation_id=? "
                "AND state='PENDING' AND approval_id=? AND boot_generation=?",
                ("APPROVED" if approve else "DENIED", *keys[:3],
                 approval_id, self.boot_generation),
            )
            return cursor.rowcount == 1

    def claim_effect(
        self, *, client_id: str, principal_id: str, invocation_id: str,
        capability_id: str, request_fingerprint: str, approval_id: str,
    ) -> bool:
        """The irreversible fence MUST commit before entering target callable."""
        keys = self._keys(client_id, principal_id, invocation_id,
                          capability_id, request_fingerprint)
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = self._get(db, keys)
            if not old or any((
                old["capability_id"] != capability_id,
                old["request_fingerprint"] != request_fingerprint,
                old["approval_id"] != approval_id,
                old["boot_generation"] != self.boot_generation,
                old["state"] != "APPROVED",
                old["expires_at"] <= self._now(),
            )):
                return False
            return db.execute(
                "UPDATE durable_hitl_pending "
                "SET state='EFFECT_STARTED', revision=revision+1 "
                "WHERE client_id=? AND principal_id=? AND invocation_id=? "
                "AND state='APPROVED' AND approval_id=? AND boot_generation=?",
                (*keys[:3], approval_id, self.boot_generation),
            ).rowcount == 1

    def inspect(
        self, *, client_id: str, principal_id: str, invocation_id: str,
    ) -> ConsentRecord | None:
        if not all((client_id, principal_id, invocation_id)):
            return None
        with self._lock, self._db() as db:
            row = self._get(db, (client_id, principal_id, invocation_id))
            return None if row is None else self._record(row)

    def recovery_disposition(self, record: ConsentRecord) -> str:
        if record.state == "EFFECT_STARTED":
            return "OUTCOME_UNKNOWN_NONREPLAYABLE"
        if record.state == "DENIED":
            return "DENIED_TERMINAL"
        if record.state == "EXPIRED" or record.expires_at <= self._now():
            return "EXPIRED_DENY"
        if record.boot_generation != self.boot_generation:
            return "REAUTHENTICATION_REQUIRED"
        if record.state == "APPROVED":
            return "APPROVED_NOT_STARTED"
        return "PENDING_HUMAN"
