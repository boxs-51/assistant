"""S04 T10: SQLite file/schema authority vs old OTP consumer (test-only).

Every database and snapshot is created inside a temporary directory. The
actual historical OTP service binary is not executed by this fixture. This
exercises file/schema ownership boundaries left unresolved after T09.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

import pytest


def _admit():
    if not (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("S04_T10_DISPOSABLE") == "1"
            and os.environ.get("S04_T10_STRICT") == "1"):
        pytest.fail("S04_HARNESS_ERROR: strict disposable CI admission required")


def _open(file: Path, timeout=1.0):
    assert file.name in ("historical.sqlite3", "pre_fence.snapshot.sqlite3")
    con=sqlite3.connect(file, timeout=timeout, isolation_level=None)
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA busy_timeout=150")
    return con


def _setup(folder: Path, purpose: str):
    assert folder.name.startswith("s04t10-")
    db=folder / "historical.sqlite3"
    c=_open(db)
    c.execute("CREATE TABLE pending_registrations(email TEXT PRIMARY KEY, purpose TEXT NOT NULL, otp TEXT NOT NULL)")
    email="t10-"+purpose+"@example.invalid"
    c.execute("INSERT INTO pending_registrations VALUES(?, ?, '123456')", (email,purpose))
    c.close()
    return db,email


def _seal(con: sqlite3.Connection):
    con.execute("""CREATE TRIGGER deny_legacy_otp_consume
        BEFORE DELETE ON pending_registrations
        BEGIN SELECT RAISE(ABORT, 'S04_SQLITE_DELETE_FENCED'); END""")


def _try_delete(con,email):
    try:
        con.execute("DELETE FROM pending_registrations WHERE email=?",(email,))
    except sqlite3.IntegrityError as exc:
        assert "S04_SQLITE_DELETE_FENCED" in str(exc), (
            "S04_HARNESS_ERROR: unexpected SQLite delete failure")
        return "FENCED"
    return "DELETED"


def _count(con):
    return con.execute("SELECT count(*) FROM pending_registrations").fetchone()[0]


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_t10_01_open_connection_sees_committed_guard(purpose):
    _admit()
    with tempfile.TemporaryDirectory(prefix="s04t10-") as root:
        db,email=_setup(Path(root),purpose)
        old=_open(db)
        cutover=_open(db)
        try:
            assert _count(old)==1
            # Old connection exists before cutover, but holds no active txn.
            _seal(cutover)
            assert _try_delete(old,email)=="FENCED"
            assert _count(cutover)==1
        finally:
            old.close();cutover.close()


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_t10_02_old_immediate_transaction_blocks_cutover_until_drained(purpose):
    _admit()
    with tempfile.TemporaryDirectory(prefix="s04t10-") as root:
        db,email=_setup(Path(root),purpose)
        old=_open(db)
        cutover=_open(db, timeout=.15)
        try:
            old.execute("BEGIN IMMEDIATE")
            try:
                _seal(cutover)
            except sqlite3.OperationalError as exc:
                assert "locked" in str(exc).lower(), (
                    "S04_HARNESS_ERROR: did not witness lock conflict")
            else:
                pytest.fail("S04_SECURITY_RED: cutover falsely ACKed while old writer held lock")
            assert cutover.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]==0
            old.execute("ROLLBACK")
            _seal(cutover)
            assert _try_delete(old,email)=="FENCED"
            assert _count(cutover)==1
        finally:
            if old.in_transaction: old.rollback()
            old.close();cutover.close()


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_t10_03_schema_write_capability_defeats_sqlite_trigger_negative_control(purpose):
    _admit()
    with tempfile.TemporaryDirectory(prefix="s04t10-") as root:
        db,email=_setup(Path(root),purpose)
        controller=_open(db)
        old=_open(db)
        try:
            _seal(controller)
            assert _try_delete(old,email)=="FENCED"
            assert _count(old)==1
            # EXPECTED HAZARD: SQLite DDL is available to any writer of DB file.
            old.execute("DROP TRIGGER deny_legacy_otp_consume")
            assert _try_delete(old,email)=="DELETED"
            assert _count(controller)==0, (
                "S04_HARNESS_ERROR: schema-edit hazard not reproduced")
        finally:
            old.close();controller.close()


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_t10_04_file_restore_reintroduces_pre_fence_authority_negative_control(purpose):
    _admit()
    with tempfile.TemporaryDirectory(prefix="s04t10-") as root:
        folder=Path(root)
        db,email=_setup(folder,purpose)
        snapshot=folder / "pre_fence.snapshot.sqlite3"
        orig=_open(db)
        backup=_open(snapshot)
        try:
            orig.backup(backup)
        finally:
            backup.close();orig.close()
        guard=_open(db)
        try:
            _seal(guard)
            assert _try_delete(guard,email)=="FENCED"
            assert _count(guard)==1
        finally:
            guard.close()
        # Both files remain in a dedicated temp directory; no live connections.
        shutil.copyfile(snapshot,db)
        reopened=_open(db)
        try:
            assert reopened.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]==0
            assert _try_delete(reopened,email)=="DELETED", (
                "S04_HARNESS_ERROR: stale SQLite backup hazard not reproduced")
            assert _count(reopened)==0
        finally:
            reopened.close()
