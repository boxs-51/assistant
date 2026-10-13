"""S1-A protected registration/quarantine ledger. NO positive issuer exists.

Only trusted, separately reviewed CRT provenance can eventually authorize an
execution. The S1-A public/private IPC never exposes a registration mutation.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import closing
from pathlib import Path
from .native_privileged_broker_s1a import S1AProtocolError, canonical_generation


class S1ARegistrationUnavailable(RuntimeError):
    pass


def _private_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise S1ARegistrationUnavailable("missing or symlinked protected directory")
    if os.name == "posix":
        info = path.stat(follow_symlinks=False)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise S1ARegistrationUnavailable("untrusted state directory DAC")


class ProtectedRegistrationLedger:
    """Epoch and immutable revoke tombstones; no API to mint native permission."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).absolute()
        _private_directory(self.directory)
        self.db_path = self.directory / "s1a_registration.db"
        if self.db_path.is_symlink():
            raise S1ARegistrationUnavailable("symlinked ledger")
        if os.name == "nt":
            # Windows ACL verification is a deployment prerequisite: an ACL
            # attestation outside Python is mandatory before any OS-positive CI.
            pass
        self.epoch = secrets.token_hex(16)
        with closing(self._db()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS s1a_epoch_log
                          (epoch TEXT PRIMARY KEY, created_unix REAL DEFAULT (unixepoch()))""")
            db.execute("""CREATE TABLE IF NOT EXISTS s1a_revocations
                          (owner_id TEXT NOT NULL, execution_id TEXT NOT NULL,
                           sandbox_id TEXT NOT NULL, generation TEXT NOT NULL,
                           PRIMARY KEY (owner_id,execution_id,sandbox_id,generation))""")
            db.execute("INSERT INTO s1a_epoch_log(epoch) VALUES (?)", (self.epoch,))

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA journal_mode=WAL")
            return db
        except BaseException:
            db.close()
            raise

    def revoke(self, *, owner_id: str, execution_id: str,
               sandbox_id: str, generation: str) -> None:
        """Only an in-process operator diagnostic; never an IPC-callable API."""
        for value in (owner_id, execution_id, sandbox_id):
            if not isinstance(value, str) or not value or value.strip() != value:
                raise S1AProtocolError("invalid revoked identity")
        gen = canonical_generation(generation)
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""INSERT OR IGNORE INTO s1a_revocations
                           (owner_id, execution_id, sandbox_id, generation)
                           VALUES (?, ?, ?, ?)""",
                           (owner_id, execution_id, sandbox_id, gen))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def authorizes(self, *_args: object, **_kwargs: object) -> bool:
        """S1-A explicitly has NO trusted CRT registration source yet."""
        return False

    def mint(self, *_args: object, **_kwargs: object) -> None:
        raise S1ARegistrationUnavailable("trusted CRT registration not implemented: DENY")

    def positive_receipt(self, *_args: object, **_kwargs: object) -> None:
        raise S1ARegistrationUnavailable("native receipt not implemented: DENY")

    def verified_empty(self, *_args: object, **_kwargs: object) -> None:
        raise S1ARegistrationUnavailable("kernel tree recovery not implemented: DENY")
