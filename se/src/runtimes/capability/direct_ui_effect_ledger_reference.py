"""Isolated SQLite *reference* for #359/#242 direct-UI one-use fencing.

NOT PRODUCTION-WIRED. No route imports this file; no authoritative credential
issuer, PostgreSQL migration, verified human attestation, or physical dispatcher
exists here. Never infer logout's physical no-post-ACK-start property from it.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class AuthorityDenied(PermissionError):
    pass


class FingerprintConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthBinding:
    """Must originate from a server-validated credential, never client JSON."""
    kind: str
    principal: str
    session: str
    generation: int

    def validated_shape(self) -> None:
        if self.kind not in {"jwt", "api_key", "guest"}:
            raise AuthorityDenied("Unknown credential kind")
        if not self.principal or not self.session or self.generation < 1:
            raise AuthorityDenied("No authoritative principal/session/generation")


def canonical_hash(arguments: dict) -> str:
    if not isinstance(arguments, dict):
        raise ValueError("Arguments must be an object")
    encoded = json.dumps(
        arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SQLiteDirectEffectReference:
    """Disposable adapter demonstrating one lock order, not a trusted issuer.

    All methods that mutate state serialize through BEGIN IMMEDIATE before any
    registry/intent read. register_trusted_session and record_human_decision are
    deliberately NOT endpoints: production integration MUST authenticate those
    operations externally and use the canonical shared UoW/migration.
    """

    def __init__(self, path: str | Path, *, boot: str | None = None, now=None):
        self.path = str(path)
        self.boot = boot or secrets.token_hex(24)
        self.now = now or time.time
        if len(self.boot) < 16:
            raise ValueError("Invalid process boot identifier")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def bootstrap_disposable_only(self) -> None:
        """NOT a migration. Tests only. Never call during gateway startup."""
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS direct_auth_ref (
                    kind TEXT NOT NULL, principal TEXT NOT NULL,
                    session TEXT NOT NULL, generation INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (kind, principal, session)
                );
                CREATE TABLE IF NOT EXISTS direct_intent_ref (
                    invocation_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL, principal TEXT NOT NULL,
                    session TEXT NOT NULL, generation INTEGER NOT NULL,
                    capability TEXT NOT NULL, version TEXT NOT NULL,
                    args_hash TEXT NOT NULL, expiry REAL NOT NULL,
                    challenge TEXT NOT NULL UNIQUE, approval_boot TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'PENDING','APPROVED','DENIED','EFFECT_STARTED')),
                    revision INTEGER NOT NULL DEFAULT 0
                );
            """)

    def register_trusted_session(self, kind: str, principal: str, session: str) -> AuthBinding:
        """TEST-ONLY registrar. No credential verification is performed here."""
        auth = AuthBinding(kind, principal, session, 1)
        auth.validated_shape()
        with self._transaction() as db:
            try:
                db.execute(
                    "INSERT INTO direct_auth_ref(kind,principal,session,generation) "
                    "VALUES (?,?,?,1)", (kind, principal, session),
                )
            except sqlite3.IntegrityError as exc:
                raise AuthorityDenied("Session identifier reuse forbidden") from exc
        return auth

    @staticmethod
    def _auth_row(db: sqlite3.Connection, auth: AuthBinding):
        auth.validated_shape()
        row = db.execute(
            "SELECT * FROM direct_auth_ref WHERE kind=? AND principal=? AND session=?",
            (auth.kind, auth.principal, auth.session),
        ).fetchone()
        if row is None or row["generation"] != auth.generation or row["revoked"]:
            raise AuthorityDenied("Absent, revoked or stale server session")
        return row

    @staticmethod
    def _intent_row(db: sqlite3.Connection, invocation_id: str):
        return db.execute(
            "SELECT * FROM direct_intent_ref WHERE invocation_id=?", (invocation_id,),
        ).fetchone()

    @staticmethod
    def _match(row, auth: AuthBinding, capability: str, version: str, args_hash: str):
        if (
            (row["kind"], row["principal"], row["session"], row["generation"])
            != (auth.kind, auth.principal, auth.session, auth.generation)
            or (row["capability"], row["version"], row["args_hash"])
            != (capability, version, args_hash)
        ):
            raise FingerprintConflict("Immutable logical invocation fingerprint changed")

    def issue_trusted_intent(
        self, auth: AuthBinding, capability: str, version: str,
        arguments: dict, *, ttl_seconds: float = 300.0,
    ) -> tuple[str, str]:
        if not capability or not version or not 0 < ttl_seconds <= 3600:
            raise ValueError("Capability/version/TTL must be authoritative and bounded")
        fingerprint = canonical_hash(arguments)
        with self._transaction() as db:
            self._auth_row(db, auth)
            invocation_id, challenge = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO direct_intent_ref "
                "(invocation_id,kind,principal,session,generation,capability,version,"
                "args_hash,expiry,challenge,approval_boot,state) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'PENDING')",
                (invocation_id, auth.kind, auth.principal, auth.session,
                 auth.generation, capability, version, fingerprint,
                 self.now() + ttl_seconds, challenge, self.boot),
            )
        return invocation_id, challenge

    def record_human_decision(
        self, auth: AuthBinding, invocation_id: str, challenge: str,
        capability: str, version: str, arguments: dict, *, approved: bool,
    ) -> str:
        """TEST-ONLY. Production needs authenticated human proof, not bool."""
        fingerprint = canonical_hash(arguments)
        with self._transaction() as db:
            self._auth_row(db, auth)  # AUTHORITY ROW FIRST
            row = self._intent_row(db, invocation_id)
            if row is None:
                raise AuthorityDenied("Unknown invocation")
            self._match(row, auth, capability, version, fingerprint)
            if row["state"] != "PENDING" or row["challenge"] != challenge:
                raise AuthorityDenied("Duplicate/stale human decision")
            if row["approval_boot"] != self.boot or row["expiry"] <= self.now():
                raise AuthorityDenied("Restart/expiry requires fresh human decision")
            state = "APPROVED" if approved else "DENIED"
            db.execute(
                "UPDATE direct_intent_ref SET state=?,revision=revision+1 "
                "WHERE invocation_id=?", (state, invocation_id),
            )
            return state

    def refresh_challenge_after_restart(
        self, auth: AuthBinding, invocation_id: str,
        capability: str, version: str, arguments: dict,
    ) -> str:
        fingerprint = canonical_hash(arguments)
        with self._transaction() as db:
            self._auth_row(db, auth)
            row = self._intent_row(db, invocation_id)
            if row is None:
                raise AuthorityDenied("Unknown invocation")
            self._match(row, auth, capability, version, fingerprint)
            if row["state"] not in ("PENDING", "APPROVED"):
                raise AuthorityDenied("Claimed/denied intent cannot be reapproved")
            if row["approval_boot"] == self.boot or row["expiry"] <= self.now():
                raise AuthorityDenied("Not a valid unexpired restart")
            challenge = secrets.token_urlsafe(32)
            db.execute(
                "UPDATE direct_intent_ref SET state='PENDING',challenge=?,"
                "approval_boot=?,revision=revision+1 WHERE invocation_id=?",
                (challenge, self.boot, invocation_id),
            )
            return challenge

    def claim_effect_once(
        self, auth: AuthBinding, invocation_id: str,
        capability: str, version: str, arguments: dict,
    ) -> str:
        fingerprint = canonical_hash(arguments)
        with self._transaction() as db:
            self._auth_row(db, auth)
            row = self._intent_row(db, invocation_id)
            if row is None:
                raise AuthorityDenied("Unknown invocation")
            self._match(row, auth, capability, version, fingerprint)
            if row["state"] == "EFFECT_STARTED":
                return "UNKNOWN_NONREPLAYABLE"  # readthrough, NEVER dispatch
            if row["state"] == "DENIED":
                return "DENIED"
            if row["expiry"] <= self.now():
                return "EXPIRED"
            if row["approval_boot"] != self.boot:
                return "FRESH_HUMAN_REQUIRED"
            if row["state"] != "APPROVED":
                return "PENDING"
            db.execute(
                "UPDATE direct_intent_ref SET state='EFFECT_STARTED',"
                "revision=revision+1 WHERE invocation_id=?", (invocation_id,),
            )
            return "EFFECT_STARTED_NEW_CLAIM"  # commit precedes any external call

    def revoke_trusted_session(self, auth: AuthBinding) -> int:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM direct_auth_ref WHERE kind=? AND principal=? AND session=?",
                (auth.kind, auth.principal, auth.session),
            ).fetchone()
            if row is None or row["generation"] != auth.generation:
                raise AuthorityDenied("Unknown/stale session; no ACK")
            if row["revoked"]:
                return row["generation"]
            next_gen = row["generation"] + 1
            db.execute(
                "UPDATE direct_auth_ref SET revoked=1,generation=?,"
                "revision=revision+1 WHERE kind=? AND principal=? AND session=?",
                (next_gen, auth.kind, auth.principal, auth.session),
            )
            return next_gen

    def inspect_disposable_only(self, invocation_id: str) -> dict | None:
        """Test diagnostic; NOT a production authorization or readthrough API."""
        with self._transaction() as db:
            row = self._intent_row(db, invocation_id)
            if row is None:
                return None
            result = dict(row)
            if result["state"] == "EFFECT_STARTED":
                result["classification"] = "UNKNOWN_NONREPLAYABLE"
            return result
