"""CTX #15 T1: real-Alembic, CTX-only owner integrity RED-first oracle.

Human Owner: CTX_T1_PATH_ACK=YES (2026-10-15).  Evidence ADD only.
No CAS/UBQ/AIC LIVE fixture, production SQL, migration, source or CI changes.

Intentionally RED on unguarded main/30a: raw SQL DELETE/REKEY, destructive
REPLACE and direct immutable Memory DML must be denied by a *future* CTX fence.
A mutation accepted and then rolled back by this test NEVER counts as a pass.
Post-fence runs may set CTX_T1_ALEMBIC_HEAD to the custodian-approved revision;
the default is the actually landed 30a, not an invented successor.
PG17 needs a real AIC_TEST_POSTGRES_URL; no PostgreSQL-to-SQLite fallback.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.context.memory import create_memory_record
from se.src.context.source_identity import ContextSourceKind, create_context_source_ref
from se.src.infrastructure.storage.repositories.memory import (
    sqlite_memory_admission_transaction,
)

ROOT = Path(__file__).resolve().parents[3]
BASE_HEAD = "30a_ctx_f5_user_wide_memory_scope"
FIELDS = (
    "memory_id", "promotion_authority_id", "memory_schema_version",
    "owner_user_id", "memory_scope", "source_context_source_id",
    "source_ref_json", "content_digest", "canonical_bytes",
    "content_json", "metadata_json", "created_at",
)
INSERT_MEMORY = (
    "INSERT INTO memory_records (" + ", ".join(FIELDS) + ") VALUES ("
    + ", ".join("?" for _ in FIELDS) + ")"
)
FIXED_TIME = datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)


def _upgrade_real_schema(url: str) -> str:
    head = os.environ.get("CTX_T1_ALEMBIC_HEAD", BASE_HEAD)
    env = dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini",
         "upgrade", head],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=240,
        check=False,
    )
    assert result.returncode == 0, (
        "Real Alembic upgrade failed", result.returncode,
        result.stdout[-3000:], result.stderr[-6000:],
    )
    return head


def _record(owner: str, tag: str, scope: str | None):
    source = create_context_source_ref(
        source_kind=ContextSourceKind.SESSION,
        authority_id="source-" + tag,
        session_id="source-" + tag,
        owner_user_id=owner,
        source_created_at=FIXED_TIME,
        source_state="active",
        metadata={"oracle": "CTX-only", "tag": tag},
    )
    record = create_memory_record(
        source_ref=source,
        promotion_authority_id="promotion-" + tag,
        memory_scope=scope,
        content={"fact": tag},
        metadata={"kind": "ctx-retained"},
    )
    data = record.model_dump(mode="json")
    row = {
        "memory_id": record.memory_id,
        "promotion_authority_id": record.promotion_authority_id,
        "memory_schema_version": record.memory_schema_version,
        "owner_user_id": record.owner_user_id,
        "memory_scope": record.memory_scope,
        "source_context_source_id": source.context_source_id,
        "source_ref_json": json.dumps(data["source_ref_snapshot"], sort_keys=True),
        "content_digest": record.content_digest,
        "canonical_bytes": record.canonical_bytes,
        "content_json": json.dumps(data["content"], sort_keys=True),
        "metadata_json": json.dumps(data["metadata"], sort_keys=True),
        "created_at": data["created_at"],
    }
    return record, row


def _connection(db: Path, fk: bool, recursive: bool) -> sqlite3.Connection:
    c = sqlite3.connect(db, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=" + ("ON" if fk else "OFF"))
    c.execute("PRAGMA recursive_triggers=" + ("ON" if recursive else "OFF"))
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == int(fk)
    assert c.execute("PRAGMA recursive_triggers").fetchone()[0] == int(recursive)
    return c


@pytest.fixture(scope="module", params=[
    (False, False), (False, True), (True, False), (True, True),
], ids=["FKoff-recursiveOFF", "FKoff-recursiveON",
        "FKon-recursiveOFF", "FKon-recursiveON"])
def real_sqlite(request, tmp_path_factory):
    fk, recursive = request.param
    db = tmp_path_factory.mktemp("ctx-t1-real") / "ctx.sqlite3"
    head = _upgrade_real_schema("sqlite+aiosqlite:///" + db.as_posix())
    with closing(_connection(db, fk, recursive)) as c:
        actual = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert actual == head, (actual, head)
        assert {
            row["name"] for row in c.execute("PRAGMA table_info(memory_records)")
        } >= set(FIELDS)
        assert c.execute(
            "SELECT COUNT(*) FROM memory_records"
        ).fetchone()[0] == 0
    return db, fk, recursive


def _insert_memory(c: sqlite3.Connection, row: dict) -> None:
    c.execute(INSERT_MEMORY, tuple(row[name] for name in FIELDS))


def _seed(real_sqlite, scope):
    db, fk, recursive = real_sqlite
    tag = uuid4().hex[:18]
    alice, bob = "ctx-a-" + tag, "ctx-b-" + tag
    ar, a = _record(alice, "alice-" + tag, scope)
    br, b = _record(bob, "bob-" + tag, scope)
    with closing(_connection(db, fk, recursive)) as c:
        c.execute(
            "INSERT INTO users(id,email,password_hash,status) "
            "VALUES (?,?,?,?)",
            (alice, alice + "@ctx.invalid", "x", "active"),
        )
        c.execute(
            "INSERT INTO users(id,email,password_hash,status) "
            "VALUES (?,?,?,?)",
            (bob, bob + "@ctx.invalid", "x", "active"),
        )
        _insert_memory(c, a)
        _insert_memory(c, b)
        c.commit()
        assert c.execute(
            "SELECT COUNT(*) FROM memory_records WHERE owner_user_id IN (?,?)",
            (alice, bob),
        ).fetchone()[0] == 2
    return alice, bob, ar, br, a, b


def _snapshot(c, alice, bob):
    users = tuple(tuple(row) for row in c.execute(
        "SELECT id,email,password_hash,status FROM users "
        "WHERE id IN (?,?) ORDER BY id", (alice, bob),
    ))
    memory = tuple(tuple(row) for row in c.execute(
        "SELECT rowid, " + ", ".join(FIELDS)
        + " FROM memory_records WHERE owner_user_id IN (?,?) "
        "ORDER BY memory_id", (alice, bob),
    ))
    return users, memory


def _probe(real_sqlite, alice, bob, sql, args=()):
    """Probe *before* rollback; assert no test-created persistence afterward."""
    db, fk, recursive = real_sqlite
    with closing(_connection(db, fk, recursive)) as c:
        before = _snapshot(c, alice, bob)
        c.execute("BEGIN")
        denied = False
        error = None
        try:
            c.execute(sql, args)
        except sqlite3.DatabaseError as exc:
            denied, error = True, type(exc).__name__ + ": " + str(exc)
        during = _snapshot(c, alice, bob)
        orphans = c.execute(
            "SELECT COUNT(*) FROM memory_records AS m "
            "LEFT JOIN users AS u ON u.id=m.owner_user_id "
            "WHERE u.id IS NULL"
        ).fetchone()[0]
        c.rollback()
    with closing(_connection(db, fk, recursive)) as fresh:
        after = _snapshot(fresh, alice, bob)
    assert after == before, "test probe persisted its own mutation"
    if denied:
        assert during == before, ("denial made partial mutations", sql)
    return {"denied": denied, "error": error, "orphans": orphans,
            "changed_in_transaction": during != before}


def _strict(results, real_sqlite):
    # Never make a green test by rolling back an *accepted* operation.
    failures = {name: result for name, result in results.items()
                if not result["denied"]}
    assert not failures, (
        "CTX_T1_PHYSICAL_RED: destructive SQL accepted on real Alembic "
        "schema; FK/recursive, cases: " + repr((real_sqlite[1:], failures))
    )


@pytest.mark.parametrize("scope", [None, "USER_WIDE"], ids=["legacy-NULL", "USER_WIDE"])
@pytest.mark.parametrize("operation", ["DELETE", "REKEY"])
def test_ctx_only_users_delete_rekey_retained_owner(real_sqlite, scope, operation):
    alice, bob, *_ = _seed(real_sqlite, scope)
    sql, args = (
        ("DELETE FROM users WHERE id=?", (alice,))
        if operation == "DELETE" else
        ("UPDATE users SET id=? WHERE id=?", ("rekey-" + alice, alice))
    )
    result = _probe(real_sqlite, alice, bob, sql, args)
    _strict({operation: result}, real_sqlite)


@pytest.mark.parametrize("scope", [None, "USER_WIDE"], ids=["legacy-NULL", "USER_WIDE"])
def test_ctx_only_users_replace_pk_email_crosskey(real_sqlite, scope):
    alice, bob, _, _, a, b = _seed(real_sqlite, scope)
    actions = {
        "same-PK": (
            "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
            "VALUES (?,?,?,?)",
            (alice, alice + "@ctx.invalid", "changed", "active"),
        ),
        "email-victim": (
            "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
            "VALUES (?,?,?,?)",
            ("replace-" + alice, bob + "@ctx.invalid", "x", "active"),
        ),
        "two-victims": (
            "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
            "VALUES (?,?,?,?)",
            (alice, bob + "@ctx.invalid", "changed", "active"),
        ),
        "update-or-replace-email": (
            "UPDATE OR REPLACE users SET email=? WHERE id=?",
            (bob + "@ctx.invalid", alice),
        ),
    }
    results = {name: _probe(real_sqlite, alice, bob, sql, values)
               for name, (sql, values) in actions.items()}
    _strict(results, real_sqlite)


@pytest.mark.parametrize("scope", [None, "USER_WIDE"], ids=["legacy-NULL", "USER_WIDE"])
def test_ctx_only_memory_replacement_and_immutability(real_sqlite, scope):
    alice, bob, _, _, a, b = _seed(real_sqlite, scope)
    cols = ", ".join(FIELDS)
    marks = ", ".join("?" for _ in FIELDS)
    replaced = {
        "identical-PK-REPLACE": dict(a),
        "authority-REPLACE": dict(a, memory_id="e" * 64),
        "cross-two-UNIQUE": dict(a, promotion_authority_id=b["promotion_authority_id"]),
    }
    actions = {
        name: ("INSERT OR REPLACE INTO memory_records (" + cols + ") VALUES ("
               + marks + ")", tuple(row[field] for field in FIELDS))
        for name, row in replaced.items()
    }
    actions.update({
        "owner-UPDATE": (
            "UPDATE memory_records SET owner_user_id=? WHERE memory_id=?",
            (bob, a["memory_id"]),
        ),
        "source-ref-UPDATE": (
            "UPDATE memory_records SET source_ref_json=? WHERE memory_id=?",
            ('{"owner_user_id":"attacker"}', a["memory_id"]),
        ),
        "same-value-UPDATE": (
            "UPDATE memory_records SET owner_user_id=owner_user_id "
            "WHERE memory_id=?", (a["memory_id"],),
        ),
        "UPSERT-DO-UPDATE": (
            "INSERT INTO memory_records (" + cols + ") VALUES (" + marks + ") "
            "ON CONFLICT(memory_id) DO UPDATE SET owner_user_id=excluded.owner_user_id",
            tuple(a[field] for field in FIELDS),
        ),
        "memory-DELETE": (
            "DELETE FROM memory_records WHERE memory_id=?", (a["memory_id"],),
        ),
    })
    results = {name: _probe(real_sqlite, alice, bob, sql, values)
               for name, (sql, values) in actions.items()}
    _strict(results, real_sqlite)


@pytest.mark.parametrize("scope", [None, "USER_WIDE"], ids=["legacy-NULL", "USER_WIDE"])
def test_ctx_only_missing_owner_and_bad_provenance_insert(real_sqlite, scope):
    alice, bob, _, _, a, _ = _seed(real_sqlite, scope)
    _, absent = _record("absent-" + alice, "absent-" + alice, scope)
    malformed = {
        "absent-owner": absent,
        "malformed-JSON": dict(a, memory_id="1" * 64,
                               promotion_authority_id="malformed-" + alice,
                               source_ref_json="{"),
        "source-owner-mismatch": dict(
            a, memory_id="2" * 64,
            promotion_authority_id="mismatch-" + alice,
            source_ref_json=json.dumps({"owner_user_id": bob,
                                        "context_source_id": a["source_context_source_id"]}),
        ),
        "JSON-array": dict(a, memory_id="3" * 64,
                           promotion_authority_id="array-" + alice,
                           source_ref_json="[]"),
    }
    # Collision-free candidate IDs are used: failure must not be a UNIQUE error.
    actions = {
        name: (INSERT_MEMORY, tuple(row[field] for field in FIELDS))
        for name, row in malformed.items()
    }
    results = {name: _probe(real_sqlite, alice, bob, sql, values)
               for name, (sql, values) in actions.items()}
    _strict(results, real_sqlite)


@pytest.mark.parametrize("scope", [None, "USER_WIDE"], ids=["legacy-NULL", "USER_WIDE"])
def test_ctx_only_real_repository_do_nothing_keeps_winner(real_sqlite, scope):
    """Positive: preserve actual _put_sqlite() SAVEPOINT + immutable readthrough."""
    db, fk, recursive = real_sqlite
    alice, bob, original, _, a, b = _seed(real_sqlite, scope)
    with closing(_connection(db, fk, recursive)) as c:
        before = _snapshot(c, alice, bob)

    async def _replay():
        engine = create_async_engine("sqlite+aiosqlite:///" + db.as_posix())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                async with sqlite_memory_admission_transaction(session) as repository:
                    winner = await repository.put(original)
                    assert winner.memory_id == original.memory_id
                    assert winner.owner_user_id == alice
                    assert winner.memory_scope == scope
        finally:
            await engine.dispose()

    asyncio.run(_replay())
    with closing(_connection(db, fk, recursive)) as c:
        assert _snapshot(c, alice, bob) == before, (
            "genuine ON CONFLICT DO NOTHING rewrote the retained winner"
        )


def test_ctx_only_sqlite_precommit_crash_rollback(real_sqlite, tmp_path):
    """Positive crash primitive: uncommitted DML must disappear on cold reopen."""
    db, fk, recursive = real_sqlite
    marker = "ctx-crash-" + uuid4().hex
    worker = (
        "import os,sqlite3,sys\n"
        "c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN IMMEDIATE')\n"
        "c.execute('INSERT INTO users(id,email,password_hash,status) "
        "VALUES (?,?,?,?)',(sys.argv[2],sys.argv[2]+'@ctx.invalid','x','active'))\n"
        "os._exit(73)\n"
    )
    p = subprocess.run([sys.executable, "-c", worker, str(db), marker],
                       cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert p.returncode == 73, (p.returncode, p.stderr)
    with closing(_connection(db, fk, recursive)) as c:
        assert c.execute("SELECT COUNT(*) FROM users WHERE id=?",
                         (marker,)).fetchone()[0] == 0


# Separate PG17 proof is strict and explicitly invoked; no fake SQLite PG pass.
# It uses independent OS processes to stage a child insert and parent mutation
# in both orders. A complete post-fence audit must additionally inspect locks,
# isolation levels, DBA roles and R1/R2 policy before claiming production SAFE.
_PG_WORKER = r"""
import asyncio, json, sys, time
from pathlib import Path
import asyncpg
dsn, sql, params_json, started, release, outcome = sys.argv[1:]
params = json.loads(params_json)
async def run():
    c = await asyncpg.connect(dsn=dsn)
    tr = c.transaction()
    await tr.start()
    Path(started).write_text("BEFORE_DML")
    try:
        await c.execute(sql, *params)
        Path(started).write_text("DML_DONE")
        until = time.monotonic() + 35
        while not Path(release).exists() and time.monotonic() < until:
            await asyncio.sleep(0.02)
        if not Path(release).exists():
            raise TimeoutError("missing process release")
        await tr.commit()
        Path(outcome).write_text("COMMITTED")
    except Exception as exc:
        await tr.rollback()
        Path(outcome).write_text("DENIED:" + type(exc).__name__)
    finally:
        await c.close()
asyncio.run(run())
"""


def _pg_pid(sql, params, url, directory, name):
    started, release, outcome = (
        directory / (name + suffix) for suffix in (".started", ".release", ".outcome")
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", _PG_WORKER, url, sql, json.dumps(params),
         str(started), str(release), str(outcome)],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return proc, started, release, outcome


def _wait_file(path, process, *, contents=None, timeout=35):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.exists() and (contents is None or path.read_text() == contents):
            return
        if process.poll() is not None:
            raise AssertionError(("PG worker exited before marker", process.returncode))
        time.sleep(0.03)
    raise AssertionError(("PG two-PID barrier timeout", str(path)))


@pytest.mark.parametrize("operation", ["DELETE", "REKEY"])
@pytest.mark.parametrize("order", ["child_first", "parent_first"])
@pytest.mark.asyncio
async def test_ctx_only_pg17_parent_child_two_pid_physical(
    tmp_path, operation, order,
):
    """PG17 physical RED-first; two independent client PIDs, both lock orders."""
    parent_url = os.environ.get("AIC_TEST_POSTGRES_URL")
    assert parent_url, "PG17 mandatory for CTX PG oracle; no skip/fallback"
    admin = create_async_engine(parent_url, isolation_level="AUTOCOMMIT")
    name = "ctx15_" + uuid4().hex[:20]
    child_db = make_url(parent_url).set(database=name).render_as_string(
        hide_password=False
    )
    pg_dsn = child_db.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        async with admin.connect() as c:
            ver = int((await c.execute(text("SHOW server_version_num"))).scalar_one())
            assert 170000 <= ver < 180000, ver
            await c.execute(text("CREATE DATABASE " + name))
        head = _upgrade_real_schema(child_db)
        engine = create_async_engine(child_db, pool_pre_ping=True)
        owner = "ctx-" + uuid4().hex
        record, memory = _record(owner, "pg-" + uuid4().hex, "USER_WIDE")
        try:
            async with engine.begin() as c:
                actual = (await c.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
                assert actual == head
                await c.execute(text(
                    "INSERT INTO users(id,email,password_hash,status) "
                    "VALUES (:id,:email,'x','active')"
                ), {"id": owner, "email": owner + "@ctx.invalid"})
            pg_cols = ",".join(FIELDS)
            pg_values = ",".join(
                "$" + str(i + 1) + ("::json" if field in (
                    "source_ref_json", "content_json", "metadata_json") else "")
                for i, field in enumerate(FIELDS)
            )
            child_sql = "INSERT INTO memory_records (" + pg_cols + ") VALUES (" + pg_values + ")"
            child_args = [memory[k] for k in FIELDS]
            parent_sql, parent_args = (
                ("DELETE FROM users WHERE id=$1", [owner])
                if operation == "DELETE"
                else ("UPDATE users SET id=$1 WHERE id=$2", ["rekey-" + owner, owner])
            )
            cproc, cstart, crelease, cout = _pg_pid(
                child_sql, child_args, pg_dsn, tmp_path, "child"
            )
            pproc, pstart, prelease, pout = _pg_pid(
                parent_sql, parent_args, pg_dsn, tmp_path, "parent"
            ) if False else (None, None, None, None)
            try:
                # Start second process only after first has staged its DML.
                if order == "child_first":
                    _wait_file(cstart, cproc, contents="DML_DONE")
                    pproc, pstart, prelease, pout = _pg_pid(
                        parent_sql, parent_args, pg_dsn, tmp_path, "parent"
                    )
                    _wait_file(pstart, pproc)  # may block at a real parent row lock
                    time.sleep(0.15)
                    crelease.touch()
                else:
                    # For parent-first, terminate the idle child and start it
                    # after the parent has staged DELETE/REKEY.
                    cproc.terminate()
                    cproc.communicate(timeout=10)
                    pproc, pstart, prelease, pout = _pg_pid(
                        parent_sql, parent_args, pg_dsn, tmp_path, "parent"
                    )
                    _wait_file(pstart, pproc, contents="DML_DONE")
                    cproc, cstart, crelease, cout = _pg_pid(
                        child_sql, child_args, pg_dsn, tmp_path, "child2"
                    )
                    _wait_file(cstart, cproc)
                    time.sleep(0.15)
                    prelease.touch()
                # The loser of a real FK race may unblock only after first COMMIT.
                _wait_file(cout if order == "child_first" else pout,
                           cproc if order == "child_first" else pproc)
                (prelease if order == "child_first" else crelease).touch()
                _wait_file(cout, cproc)
                _wait_file(pout, pproc)
                assert cproc.wait(timeout=20) == 0
                assert pproc.wait(timeout=20) == 0
                states = (cout.read_text(), pout.read_text())
                async with engine.connect() as c:
                    orphans = (await c.execute(text(
                        "SELECT COUNT(*) FROM memory_records m LEFT JOIN users u "
                        "ON u.id=m.owner_user_id WHERE m.memory_id=:m AND u.id IS NULL"
                    ), {"m": record.memory_id})).scalar_one()
                assert states != ("COMMITTED", "COMMITTED") and orphans == 0, (
                    "CTX_PG17_PHYSICAL_RED two-PID parent/child race", order,
                    operation, states, orphans,
                )
            finally:
                for proc in (cproc, pproc):
                    if proc is not None and proc.poll() is None:
                        proc.kill()
                        proc.communicate(timeout=10)
        finally:
            await engine.dispose()
    finally:
        async with admin.connect() as c:
            await c.execute(text("DROP DATABASE IF EXISTS " + name + " WITH (FORCE)"))
        await admin.dispose()
