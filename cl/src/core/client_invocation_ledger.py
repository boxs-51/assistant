from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Optional


class ClientInvocationLedgerState(str, Enum):
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    TERMINAL = "TERMINAL"


class ClientInvocationLedgerConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ClientInvocationRecord:
    client_id: str
    principal_id: str
    invocation_id: str
    capability_id: str
    capability_version: str
    request_fingerprint: str
    idempotency: str
    state: ClientInvocationLedgerState
    terminal_type: str | None
    terminal_payload: dict[str, Any] | None
    created_at: float
    updated_at: float
    expires_at: float


class ClientInvocationLedger:
    """Durable SQLite authority for client-side remote invocation recovery."""

    def __init__(
        self,
        path: Optional[Path | str] = None,
        *,
        terminal_ttl: float = 30 * 24 * 60 * 60,
    ) -> None:
        if terminal_ttl <= 0:
            raise ValueError("terminal_ttl must be > 0")
        self.path = Path(path) if path else self.default_path()
        self.terminal_ttl = float(terminal_ttl)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def default_path() -> Path:
        override = os.environ.get("ASSISTANT_CLIENT_INVOCATION_LEDGER_PATH")
        if override:
            return Path(override).expanduser()
        if os.name == "nt":
            root = Path(
                os.environ.get(
                    "LOCALAPPDATA",
                    Path.home() / "AppData" / "Local",
                )
            )
        else:
            root = Path(
                os.environ.get(
                    "XDG_STATE_HOME",
                    Path.home() / ".local" / "state",
                )
            )
        return root / "AssistantClient" / "client-invocations.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Own one SQLite handle for exactly one ledger operation.

        sqlite3.Connection.__exit__ commits/rolls back but does not close the
        connection. R6-D must close every handle deterministically, otherwise
        Windows keeps the SQLite file locked after ClientRuntime shutdown.
        """
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS client_invocations (
                    client_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    invocation_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    capability_version TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    idempotency TEXT NOT NULL,
                    state TEXT NOT NULL,
                    terminal_type TEXT,
                    terminal_payload TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (client_id, principal_id, invocation_id),
                    CHECK (state IN ('PREPARED', 'RUNNING', 'TERMINAL'))
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_client_invocations_expiry
                ON client_invocations (state, expires_at)
                """
            )

    def prepare(
        self,
        *,
        client_id: str,
        principal_id: str,
        invocation_id: str,
        capability_id: str,
        capability_version: str,
        request_fingerprint: str,
        idempotency: str,
    ) -> ClientInvocationRecord:
        self._require_identity(client_id, principal_id, invocation_id)
        now = time.time()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._select(
                connection,
                client_id,
                principal_id,
                invocation_id,
            )
            if existing is not None:
                self._validate_semantics(
                    existing,
                    capability_id=capability_id,
                    capability_version=capability_version,
                    request_fingerprint=request_fingerprint,
                    idempotency=idempotency,
                )
                return existing
            connection.execute(
                """
                INSERT INTO client_invocations (
                    client_id,
                    principal_id,
                    invocation_id,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                    idempotency,
                    state,
                    terminal_type,
                    terminal_payload,
                    created_at,
                    updated_at,
                    expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    client_id,
                    principal_id,
                    invocation_id,
                    capability_id,
                    capability_version,
                    request_fingerprint,
                    idempotency,
                    ClientInvocationLedgerState.PREPARED.value,
                    now,
                    now,
                    now + self.terminal_ttl,
                ),
            )
            record = self._select(
                connection,
                client_id,
                principal_id,
                invocation_id,
            )
            assert record is not None
            return record

    def mark_running(
        self,
        *,
        client_id: str,
        principal_id: str,
        invocation_id: str,
    ) -> ClientInvocationRecord:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._require_record(
                connection,
                client_id,
                principal_id,
                invocation_id,
            )
            if existing.state is ClientInvocationLedgerState.TERMINAL:
                raise ClientInvocationLedgerConflict(
                    "Terminal invocation cannot return to RUNNING."
                )
            if existing.state is ClientInvocationLedgerState.PREPARED:
                connection.execute(
                    """
                    UPDATE client_invocations
                    SET state = ?, updated_at = ?
                    WHERE client_id = ?
                      AND principal_id = ?
                      AND invocation_id = ?
                      AND state = ?
                    """,
                    (
                        ClientInvocationLedgerState.RUNNING.value,
                        time.time(),
                        client_id,
                        principal_id,
                        invocation_id,
                        ClientInvocationLedgerState.PREPARED.value,
                    ),
                )
            return self._require_record(
                connection,
                client_id,
                principal_id,
                invocation_id,
            )

    def commit_terminal(
        self,
        *,
        client_id: str,
        principal_id: str,
        invocation_id: str,
        terminal_type: str,
        terminal_payload: dict[str, Any],
    ) -> ClientInvocationRecord:
        if terminal_type not in {"result", "error", "cancelled"}:
            raise ValueError("Unsupported terminal_type.")
        payload_json = self._encode_payload(terminal_payload)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._require_record(
                connection,
                client_id,
                principal_id,
                invocation_id,
            )
            if existing.state is ClientInvocationLedgerState.TERMINAL:
                if (
                    existing.terminal_type != terminal_type
                    or self._encode_payload(existing.terminal_payload or {})
                    != payload_json
                ):
                    raise ClientInvocationLedgerConflict(
                        "Terminal invocation outcome is immutable."
                    )
                return existing
            now = time.time()
            connection.execute(
                """
                UPDATE client_invocations
                SET state = ?,
                    terminal_type = ?,
                    terminal_payload = ?,
                    updated_at = ?,
                    expires_at = ?
                WHERE client_id = ?
                  AND principal_id = ?
                  AND invocation_id = ?
                  AND state IN (?, ?)
                """,
                (
                    ClientInvocationLedgerState.TERMINAL.value,
                    terminal_type,
                    payload_json,
                    now,
                    now + self.terminal_ttl,
                    client_id,
                    principal_id,
                    invocation_id,
                    ClientInvocationLedgerState.PREPARED.value,
                    ClientInvocationLedgerState.RUNNING.value,
                ),
            )
            return self._require_record(
                connection,
                client_id,
                principal_id,
                invocation_id,
            )

    def get(
        self,
        *,
        client_id: str,
        principal_id: str,
        invocation_id: str,
    ) -> ClientInvocationRecord | None:
        if not client_id or not principal_id or not invocation_id:
            return None
        with self._lock, self._connection() as connection:
            return self._select(
                connection,
                client_id,
                principal_id,
                invocation_id,
            )

    def purge_expired(self, *, now: float | None = None) -> int:
        """GC terminal rows only; never erase RUNNING crash evidence."""
        cutoff = time.time() if now is None else float(now)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                DELETE FROM client_invocations
                WHERE state = ?
                  AND expires_at <= ?
                """,
                (
                    ClientInvocationLedgerState.TERMINAL.value,
                    cutoff,
                ),
            )
            return int(cursor.rowcount)

    @staticmethod
    def _require_identity(
        client_id: str,
        principal_id: str,
        invocation_id: str,
    ) -> None:
        if not client_id or not principal_id or not invocation_id:
            raise ValueError(
                "client_id, principal_id and invocation_id are required."
            )

    def _require_record(
        self,
        connection: sqlite3.Connection,
        client_id: str,
        principal_id: str,
        invocation_id: str,
    ) -> ClientInvocationRecord:
        record = self._select(
            connection,
            client_id,
            principal_id,
            invocation_id,
        )
        if record is None:
            raise LookupError(
                f"Unknown client invocation '{invocation_id}'."
            )
        return record

    @staticmethod
    def _validate_semantics(
        existing: ClientInvocationRecord,
        *,
        capability_id: str,
        capability_version: str,
        request_fingerprint: str,
        idempotency: str,
    ) -> None:
        if (
            existing.capability_id != capability_id
            or existing.capability_version != capability_version
            or existing.request_fingerprint != request_fingerprint
            or existing.idempotency != idempotency
        ):
            raise ClientInvocationLedgerConflict(
                "invocation_id is already bound to different request semantics."
            )

    def _select(
        self,
        connection: sqlite3.Connection,
        client_id: str,
        principal_id: str,
        invocation_id: str,
    ) -> ClientInvocationRecord | None:
        row = connection.execute(
            """
            SELECT *
            FROM client_invocations
            WHERE client_id = ?
              AND principal_id = ?
              AND invocation_id = ?
            """,
            (
                client_id,
                principal_id,
                invocation_id,
            ),
        ).fetchone()
        if row is None:
            return None
        return ClientInvocationRecord(
            client_id=row["client_id"],
            principal_id=row["principal_id"],
            invocation_id=row["invocation_id"],
            capability_id=row["capability_id"],
            capability_version=row["capability_version"],
            request_fingerprint=row["request_fingerprint"],
            idempotency=row["idempotency"],
            state=ClientInvocationLedgerState(row["state"]),
            terminal_type=row["terminal_type"],
            terminal_payload=(
                json.loads(row["terminal_payload"])
                if row["terminal_payload"] is not None
                else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=float(row["expires_at"]),
        )

    @staticmethod
    def _encode_payload(payload: dict[str, Any]) -> str:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


__all__ = [
    "ClientInvocationLedger",
    "ClientInvocationLedgerConflict",
    "ClientInvocationLedgerState",
    "ClientInvocationRecord",
]
