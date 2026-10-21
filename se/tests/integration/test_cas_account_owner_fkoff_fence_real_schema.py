"""CAS #74 T1: physical owner-integrity RED-first regression on actual Alembic schema.

Scoped to evidence only. This test deliberately fails whenever a protected
FileAsset owner or ProviderBinding association can be deleted, rekeyed,
replaced or reassigned, even if SQLite foreign_keys/recursive_triggers are OFF.
It never commits a destructive probe. No xfail/skip/mock schema/PG fallback.

Stack dependency: unmerged AIC 31a->31b from evidence PR #523; production
successor fences and R1/R2 erasure policy are NOT included in this file.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_HEAD = "32b_alpha_s03_owner_integrity"
FILE_STATES = (
    "STAGING", "READY", "QUARANTINED", "DELETING", "DELETED", "ERROR",
)
MODES = (
    pytest.param("sqlite_off", 0, id="sqlite-FKoff-recursive-off"),
    pytest.param("sqlite_off", 1, id="sqlite-FKoff-recursive-on"),
    pytest.param("sqlite_on", 0, id="sqlite-FKon-recursive-off"),
    pytest.param("sqlite_on", 1, id="sqlite-FKon-recursive-on"),
    pytest.param("postgresql", None, id="postgresql17"),
)


def _upgrade_real_schema(url: str) -> None:
    env = dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url)
    child = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", ALEMBIC_HEAD],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240,
        check=False,
    )
    assert child.returncode == 0, (
        "REAL_ALEMBIC_UPGRADE_FAILED", child.returncode,
        child.stdout[-2500:], child.stderr[-6000:],
    )


async def _mode(conn, backend: str, recursive: int | None) -> None:
    if backend == "postgresql":
        return
    await conn.exec_driver_sql(
        "PRAGMA foreign_keys=" + ("ON" if backend == "sqlite_on" else "OFF")
    )
    await conn.exec_driver_sql(f"PRAGMA recursive_triggers={recursive}")
    fk = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar_one()
    rt = (await conn.exec_driver_sql("PRAGMA recursive_triggers")).scalar_one()
    assert (fk, rt) == (int(backend == "sqlite_on"), recursive), (fk, rt)


async def _scalar(engine, sql: str, params=None):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).scalar_one()


async def _assert_persisted_original(engine, files: dict[str, str], bob_file: str):
    async with engine.connect() as conn:
        users = (await conn.execute(text(
            "SELECT id,email,password_hash FROM users ORDER BY id"
        ))).all()
        assert users == [
            ("alice", "alice@cas.invalid", "original-a"),
            ("bob", "bob@cas.invalid", "original-b"),
        ], users
        for asset_id in (*files.values(), bob_file):
            expected = "bob" if asset_id == bob_file else "alice"
            owner = (await conn.execute(text(
                "SELECT owner_user_id FROM files WHERE id=:f"
            ), {"f": asset_id})).scalar_one()
            assert owner == expected, (asset_id, owner)
            file_id = (await conn.execute(text(
                "SELECT file_id FROM file_provider_bindings WHERE id=:b"
            ), {"b": "binding-" + asset_id})).scalar_one()
            assert file_id == asset_id, (asset_id, file_id)


async def _probe(engine, backend, recursive, name, statement, params=None):
    """Require a real SQL rejection, and always ROLLBACK any successful mutation."""
    async with engine.connect() as conn:
        await _mode(conn, backend, recursive)
        denied = False
        observation = {}
        try:
            result = await conn.execute(text(statement), params or {})
            observation["affected_rows"] = result.rowcount
            observation["cas_orphans"] = (await conn.execute(text(
                "SELECT COUNT(*) FROM files f LEFT JOIN users u "
                "ON f.owner_user_id=u.id WHERE u.id IS NULL"
            ))).scalar_one()
            observation["binding_orphans"] = (await conn.execute(text(
                "SELECT COUNT(*) FROM file_provider_bindings b "
                "LEFT JOIN files f ON b.file_id=f.id WHERE f.id IS NULL"
            ))).scalar_one()
            observation["alice_exists"] = (await conn.execute(text(
                "SELECT COUNT(*) FROM users WHERE id='alice'"
            ))).scalar_one()
            observation["bob_exists"] = (await conn.execute(text(
                "SELECT COUNT(*) FROM users WHERE id='bob'"
            ))).scalar_one()
        except DBAPIError as exc:
            denied = True
            observation["db_error_type"] = type(exc.orig).__name__
        finally:
            await conn.rollback()
    return {"case": name, "denied": denied, **observation}


async def _assert_user_do_nothing(engine, backend, recursive):
    # A legitimate conflicting INSERT ... DO NOTHING must remain a true no-op;
    # this is NOT REPLACE, DO UPDATE or authenticated new-user enrollment.
    async with engine.connect() as conn:
        await _mode(conn, backend, recursive)
        await conn.execute(text(
            "INSERT INTO users(id,email,password_hash,status) "
            "VALUES ('alice','duplicate-should-not-win@cas.invalid','poison','active') "
            "ON CONFLICT DO NOTHING"
        ))
        row = (await conn.execute(text(
            "SELECT email,password_hash FROM users WHERE id='alice'"
        ))).one()
        assert row == ("alice@cas.invalid", "original-a"), row
        await conn.rollback()


async def _hard_exit_uncommitted_owner_relink(
    engine, url, backend, recursive, asset_id
):
    # Different physical PID, DB client, no SQLAlchemy UoW or mocked crash.
    worker = r"""
import asyncio, os, sqlite3, sys
url,backend,recursive,asset_id=sys.argv[1:]
if backend.startswith("sqlite"):
    from sqlalchemy.engine import make_url
    conn=sqlite3.connect(make_url(url).database, timeout=20)
    conn.execute("PRAGMA foreign_keys="+("ON" if backend=="sqlite_on" else "OFF"))
    conn.execute("PRAGMA recursive_triggers="+recursive)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE files SET owner_user_id='bob' WHERE id=?", (asset_id,))
        print("PRECOMMIT_UNSAFE_OWNER_RELINK",flush=True)
    except sqlite3.DatabaseError:
        print("PRECOMMIT_OWNER_RELINK_DENIED",flush=True)
    os._exit(75)
else:
    import asyncpg
    async def run():
        conn=await asyncpg.connect(dsn=url.replace("postgresql+asyncpg://","postgresql://"))
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                "UPDATE files SET owner_user_id='bob' WHERE id=$1",asset_id
            )
            print("PRECOMMIT_UNSAFE_OWNER_RELINK",flush=True)
        except Exception:
            print("PRECOMMIT_OWNER_RELINK_DENIED",flush=True)
        os._exit(75)
    asyncio.run(run())
"""
    child = subprocess.run(
        [sys.executable, "-c", worker, url, backend, str(recursive), asset_id],
        cwd=str(ROOT), capture_output=True, text=True, timeout=65, check=False,
    )
    assert child.returncode == 75, (
        "PHYSICAL_PRECOMMIT_PROCESS_EXIT", child.returncode,
        child.stdout, child.stderr,
    )
    assert "PRECOMMIT_" in child.stdout, child.stdout
    assert await _scalar(
        engine, "SELECT owner_user_id FROM files WHERE id=:f", {"f": asset_id}
    ) == "alice"


@pytest.mark.parametrize(("backend", "recursive"), MODES)
@pytest.mark.parametrize("with_ubq_history", (False, True), ids=("CAS-only", "CAS-and-UBQ"))
@pytest.mark.asyncio
async def test_cas_retained_owner_fkoff_and_provider_link_physical_guard(
    tmp_path, backend, recursive, with_ubq_history
):
    """All six states; direct DML and cross-key REPLACE; real SQLite/PG17."""
    name = "cast1_" + uuid4().hex[:14]
    admin = None
    if backend == "postgresql":
        parent = os.environ.get("AIC_TEST_POSTGRES_URL")
        assert parent, "PG17 MANDATORY: configure AIC_TEST_POSTGRES_URL; no fallback"
        admin = create_async_engine(parent, isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            version = int((await conn.execute(text("SHOW server_version_num"))).scalar_one())
            assert 170000 <= version < 180000, ("PG17 required", version)
            await conn.execute(text("CREATE DATABASE " + name))
        url = make_url(parent).set(database=name).render_as_string(
            hide_password=False
        )
    else:
        url = "sqlite+aiosqlite:///" + str(tmp_path / (name + ".db"))

    engine = create_async_engine(url, pool_pre_ping=True)
    files = {state: f"cas-{name}-{state.lower()}" for state in FILE_STATES}
    bob_file = "cas-" + name + "-bob"
    retired = "retired-" + name
    try:
        _upgrade_real_schema(url)
        await engine.dispose()
        assert await _scalar(engine, "SELECT version_num FROM alembic_version") == ALEMBIC_HEAD

        async with engine.begin() as conn:
            await _mode(conn, backend, recursive)
            await conn.execute(text(
                "INSERT INTO users(id,email,password_hash,status) VALUES "
                "('alice','alice@cas.invalid','original-a','active'),"
                "('bob','bob@cas.invalid','original-b','active')"
            ))
            for state, asset_id in (
                *((state, file_id) for state, file_id in files.items()),
                ("READY", bob_file),
            ):
                blob_id = "blob-" + asset_id
                await conn.execute(text(
                    "INSERT INTO file_blobs(id,storage_backend,object_key,state) "
                    "VALUES (:id,'local',:key,'STAGING')"
                ), {"id": blob_id, "key": blob_id})
                await conn.execute(text(
                    "INSERT INTO files "
                    "(id,owner_user_id,blob_id,filename,mime_type,origin_type,state) "
                    "VALUES (:id,:owner,:blob,'file.txt','text/plain','USER_UPLOAD',:state)"
                ), {
                    "id": asset_id, "owner": "bob" if asset_id == bob_file else "alice",
                    "blob": blob_id, "state": state,
                })
                await conn.execute(text(
                    "INSERT INTO file_provider_bindings "
                    "(id,file_id,provider_name,provider_namespace,state,live_claim_token) "
                    "VALUES (:id,:file,'test',:ns,'PROCESSING','LIVE')"
                ), {
                    "id": "binding-" + asset_id,
                    "file": asset_id, "ns": asset_id,
                })
            if with_ubq_history:
                await conn.execute(text(
                    "INSERT INTO agent_tasks "
                    "(id,session_id,created_by,assigned_agent_id,status,wait_reasons,input) "
                    "VALUES (:id,'session','alice','agent','CREATED','[]','{}')"
                ), {"id": "task-" + name})
                await conn.execute(text(
                    "INSERT INTO user_budget_policies "
                    "(policy_id,owner_user_id,policy_version,policy_fingerprint,"
                    "window_duration_seconds,tool_limits_json) "
                    "VALUES (:id,'alice','v1',:fp,3600,'{}')"
                ), {"id": "policy-" + name, "fp": "a" * 64})
                await conn.execute(text(
                    "INSERT INTO user_budget_accounts(owner_user_id,next_window_epoch) "
                    "VALUES ('alice',1)"
                ))
            await conn.execute(text(
                "INSERT INTO aic_instance_reservations(agent_instance_id,state,generation) "
                "VALUES (:id,'ACTIVE',1)"
            ), {"id": retired})
            await conn.execute(text(
                "INSERT INTO aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,definition_version,"
                "lifecycle_state,generation) "
                "VALUES (:id,'alice','def','v1','ACTIVE',1)"
            ), {"id": retired})

        # Demonstrate a proper, non-reusable AIC retired identity first.
        async with engine.begin() as conn:
            await _mode(conn, backend, recursive)
            await conn.execute(text(
                "UPDATE aic_instance_reservations SET state='RETIRING' "
                "WHERE agent_instance_id=:id"
            ), {"id": retired})
            await conn.execute(text(
                "DELETE FROM aic_instance_live WHERE agent_instance_id=:id"
            ), {"id": retired})
            await conn.execute(text(
                "UPDATE aic_instance_reservations SET state='RETIRED',generation=2 "
                "WHERE agent_instance_id=:id"
            ), {"id": retired})
        assert await _scalar(engine,
            "SELECT state FROM aic_instance_reservations WHERE agent_instance_id=:id",
            {"id": retired},
        ) == "RETIRED"
        await _assert_persisted_original(engine, files, bob_file)
        await _assert_user_do_nothing(engine, backend, recursive)

        mutations = [
            ("users-delete", "DELETE FROM users WHERE id='alice'", {}),
            ("users-id-rekey", "UPDATE users SET id='alice-new' WHERE id='alice'", {}),
        ]
        for state, asset_id in files.items():
            mutations.extend((
                (
                    "file-owner-transfer-" + state,
                    "UPDATE files SET owner_user_id='bob' WHERE id=:f",
                    {"f": asset_id},
                ),
                (
                    "provider-file-relink-" + state,
                    "UPDATE file_provider_bindings SET file_id=:other WHERE id=:b",
                    {"other": bob_file, "b": "binding-" + asset_id},
                ),
                (
                    "file-id-rekey-" + state,
                    "UPDATE files SET id=:new WHERE id=:f",
                    {"new": asset_id + "-new", "f": asset_id},
                ),
            ))
        if backend != "postgresql":
            mutations.extend((
                (
                    "users-replace-pk-same-id-different-incarnation",
                    "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
                    "VALUES ('alice','alice-new@cas.invalid','poison','active')", {},
                ),
                (
                    "users-replace-email-victim",
                    "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
                    "VALUES ('outsider','alice@cas.invalid','poison','active')", {},
                ),
                (
                    "users-replace-two-victims-id-and-email",
                    "INSERT OR REPLACE INTO users(id,email,password_hash,status) "
                    "VALUES ('alice','bob@cas.invalid','poison','active')", {},
                ),
                (
                    "users-update-or-replace-cross-email-victim",
                    "UPDATE OR REPLACE users SET email='bob@cas.invalid' "
                    "WHERE id='alice'", {},
                ),
            ))

        results = []
        for label, sql, params in mutations:
            results.append(await _probe(engine, backend, recursive, label, sql, params))
            await _assert_persisted_original(engine, files, bob_file)

        await _hard_exit_uncommitted_owner_relink(
            engine, url, backend, recursive, files["STAGING"]
        )
        await _assert_persisted_original(engine, files, bob_file)

        # No silent no-op/skip/xfail success: every protected DML MUST be
        # rejected by the database. A successful mutation is a P0 RED,
        # even when a later rollback leaves the disposable DB consistent.
        violated = [item for item in results if not item["denied"]]
        print("CAS_T1_REAL_SCHEMA", backend, recursive, with_ubq_history, results)
        assert not violated, (
            "CAS_T1_P0_RAW_SQL_MUTATION_ACCEPTED; expected physical DENY; "
            "SQLite FK/recursive, real schema, after AIC RETIRED: "
            + repr((backend, recursive, with_ubq_history, violated))
        )
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.connect() as conn:
                await conn.execute(text("DROP DATABASE IF EXISTS " + name + " WITH (FORCE)"))
            await admin.dispose()
