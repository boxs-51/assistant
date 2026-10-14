"""Cross-owner retention gate on REAL Alembic-rebuilt 31b + CAS + UBQ schema.

Evidence-only RED-first test. A DELETE/REKEY that would orphan a CAS asset
is a genuine failure, not an xfail or a synthetic reproduction. Disposable DBs.
No production migration/source change. No privileged production endpoint.
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
HEAD = "31b_aic0_pg_guard_forward"


def alembic_upgrade(url):
    env = dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", HEAD],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout[-2000:], proc.stderr[-6000:])


async def sqlite_mode(conn, backend):
    if backend.startswith("sqlite"):
        await conn.exec_driver_sql(
            "PRAGMA foreign_keys=" + ("ON" if backend == "sqlite_on" else "OFF")
        )
        actual = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar_one()
        assert actual == int(backend == "sqlite_on"), (backend, actual)


async def scalar(engine, statement, params=None):
    async with engine.connect() as c:
        return (await c.execute(text(statement), params or {})).scalar_one()


@pytest.mark.parametrize("backend", ("sqlite_off", "sqlite_on", "postgresql"))
@pytest.mark.parametrize("with_ubq_task_history", (False, True))
@pytest.mark.asyncio
async def test_real_schema_retired_aic_cas_provider_ubq_delete_rekey_and_hardexit(
    tmp_path, backend, with_ubq_task_history
):
    db_name = "lifecycle_" + uuid4().hex[:14]
    admin = None
    if backend == "postgresql":
        parent = os.environ.get("AIC_TEST_POSTGRES_URL")
        assert parent, "PG17 mandatory; no skip/fallback"
        admin = create_async_engine(parent, isolation_level="AUTOCOMMIT")
        async with admin.connect() as c:
            ver = int((await c.execute(text("SHOW server_version_num"))).scalar_one())
            assert 170000 <= ver < 180000, ver
            await c.execute(text("CREATE DATABASE " + db_name))
        url = make_url(parent).set(database=db_name).render_as_string(hide_password=False)
    else:
        url = "sqlite+aiosqlite:///" + str(tmp_path / (db_name + ".db"))
    engine = create_async_engine(url, pool_pre_ping=True)
    marker = "aic-retired-" + db_name
    policy = "ubq-policy-" + db_name
    task = "task-" + db_name
    blob = "blob-" + db_name
    asset = "asset-" + db_name
    binding = "binding-" + db_name
    try:
        alembic_upgrade(url)
        await engine.dispose()  # CLI process may have replaced schema + PG prepared plans
        assert await scalar(engine, "SELECT version_num FROM alembic_version") == HEAD

        # All records come from physical historical Alembic revisions.
        async with engine.begin() as c:
            await sqlite_mode(c, backend)
            await c.execute(text(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice@lifecycle.invalid','x','active')"
            ))
            await c.execute(text(
                "INSERT INTO file_blobs(id,storage_backend,object_key,state) "
                "VALUES (:b,'local',:k,'STAGING')"
            ), {"b": blob, "k": blob})
            await c.execute(text(
                "INSERT INTO files(id,owner_user_id,blob_id,filename,mime_type,origin_type,state) "
                "VALUES (:f,'alice',:b,'file.txt','text/plain','USER_UPLOAD','STAGING')"
            ), {"f": asset, "b": blob})
            await c.execute(text(
                "INSERT INTO file_provider_bindings "
                "(id,file_id,provider_name,provider_namespace,state,live_claim_token) "
                "VALUES (:i,:f,'test','namespace','PROCESSING','LIVE')"
            ), {"i": binding, "f": asset})
            if with_ubq_task_history:
                await c.execute(text(
                    "INSERT INTO agent_tasks "
                    "(id,session_id,created_by,assigned_agent_id,status,wait_reasons,input) "
                    "VALUES (:t,'session','alice','agent','CREATED','[]','{}')"
                ), {"t": task})
                await c.execute(text(
                    "INSERT INTO user_budget_policies "
                    "(policy_id,owner_user_id,policy_version,policy_fingerprint,"
                    "window_duration_seconds,tool_limits_json) "
                    "VALUES (:p,'alice','v1',:fp,3600,'{}')"
                ), {"p": policy, "fp": "a" * 64})
                await c.execute(text(
                    "INSERT INTO user_budget_accounts(owner_user_id,next_window_epoch) "
                    "VALUES ('alice',1)"
                ))
            await c.execute(text(
                "INSERT INTO aic_instance_reservations "
                "(agent_instance_id,state,generation) VALUES (:i,'ACTIVE',1)"
            ), {"i": marker})
            await c.execute(text(
                "INSERT INTO aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,definition_version,"
                "lifecycle_state,generation) "
                "VALUES (:i,'alice','def','v1','ACTIVE',1)"
            ), {"i": marker})

        # LIVE must prevent physical owner deletion regardless of FK setting.
        async with engine.connect() as c:
            await sqlite_mode(c, backend)
            denied_live = False
            try:
                await c.execute(text("DELETE FROM users WHERE id='alice'"))
            except DBAPIError:
                denied_live = True
            await c.rollback()
            assert denied_live, "AIC LIVE owner DELETE unexpectedly succeeded"

        async with engine.begin() as c:
            await sqlite_mode(c, backend)
            await c.execute(text(
                "UPDATE aic_instance_reservations SET state='RETIRING' "
                "WHERE agent_instance_id=:i"
            ), {"i": marker})
            await c.execute(text(
                "DELETE FROM aic_instance_live WHERE agent_instance_id=:i"
            ), {"i": marker})
            await c.execute(text(
                "UPDATE aic_instance_reservations SET state='RETIRED',generation=2 "
                "WHERE agent_instance_id=:i"
            ), {"i": marker})
        assert await scalar(engine,
            "SELECT state FROM aic_instance_reservations WHERE agent_instance_id=:i",
            {"i": marker}) == "RETIRED"

        # Force direct SQL against actual persisted schema. Roll back every
        # potentially unsafe experiment; compute orphan count IN transaction.
        results = {}
        for op, stmt in (
            ("rekey", "UPDATE users SET id='renamed' WHERE id='alice'"),
            ("delete", "DELETE FROM users WHERE id='alice'"),
        ):
            async with engine.connect() as c:
                await sqlite_mode(c, backend)
                denied = False
                orphan_count = 0
                try:
                    await c.execute(text(stmt))
                    orphan_count = (await c.execute(text(
                        "SELECT COUNT(*) FROM files AS f "
                        "LEFT JOIN users AS u ON f.owner_user_id=u.id "
                        "WHERE u.id IS NULL"
                    ))).scalar_one()
                except DBAPIError:
                    denied = True
                finally:
                    await c.rollback()
                results[op] = (denied, orphan_count)

        # A real independent child PID must not commit its pre-crash changes.
        # The child uses sqlite3/asyncpg drivers, not a fake SQLAlchemy session.
        worker = r"""
import asyncio,os,sqlite3,sys
url,backend=sys.argv[1:]
if backend.startswith("sqlite"):
    from sqlalchemy.engine import make_url
    path=make_url(url).database
    c=sqlite3.connect(path)
    c.execute("PRAGMA foreign_keys="+("ON" if backend=="sqlite_on" else "OFF"))
    c.execute("BEGIN")
    try:
        c.execute("DELETE FROM users WHERE id='alice'")
        print("PRECOMMIT_OWNER_DELETE_ATTEMPT",flush=True)
    except sqlite3.DatabaseError:
        print("PRECOMMIT_DELETE_DENIED",flush=True)
    os._exit(73)
else:
    import asyncpg
    async def run():
        dsn=url.replace("postgresql+asyncpg://","postgresql://")
        c=await asyncpg.connect(dsn=dsn)
        await c.execute("BEGIN")
        try:
            await c.execute("DELETE FROM users WHERE id='alice'")
            print("PRECOMMIT_OWNER_DELETE_ATTEMPT",flush=True)
        except Exception:
            print("PRECOMMIT_DELETE_DENIED",flush=True)
        os._exit(73)
    asyncio.run(run())
"""
        child = subprocess.run(
            [sys.executable, "-c", worker, url, backend],
            cwd=ROOT, capture_output=True, text=True, timeout=50,
        )
        assert child.returncode == 73, (
            child.returncode, child.stdout, child.stderr
        )
        assert "PRECOMMIT_" in child.stdout, child.stdout
        assert await scalar(engine, "SELECT COUNT(*) FROM users WHERE id='alice'") == 1
        assert await scalar(engine,
            "SELECT COUNT(*) FROM files WHERE id=:f AND owner_user_id='alice'",
            {"f": asset}) == 1
        assert await scalar(engine,
            "SELECT COUNT(*) FROM file_provider_bindings WHERE id=:i AND file_id=:f",
            {"i": binding, "f": asset}) == 1
        assert await scalar(engine,
            "SELECT COUNT(*) FROM aic_instance_reservations "
            "WHERE agent_instance_id=:i AND state='RETIRED'", {"i": marker}) == 1
        if with_ubq_task_history:
            assert await scalar(engine,
                "SELECT COUNT(*) FROM user_budget_policies WHERE policy_id=:p",
                {"p": policy}) == 1
            assert await scalar(engine,
                "SELECT COUNT(*) FROM agent_tasks WHERE id=:t", {"t": task}) == 1

        # Persistent opaque AIC reservation may NEVER be rebound after retire.
        async with engine.connect() as c:
            await sqlite_mode(c, backend)
            rejected = False
            try:
                await c.execute(text(
                    "INSERT INTO aic_instance_live "
                    "(agent_instance_id,owner_user_id,definition_id,definition_version,"
                    "lifecycle_state,generation) "
                    "VALUES (:i,'alice','rebound','v2','ACTIVE',1)"
                ), {"i": marker})
            except DBAPIError:
                rejected = True
            finally:
                await c.rollback()
            assert rejected, "RETIRED identity reuse accepted"

        # Integrity contract: a retained CAS owner must not be silently orphaned.
        # Fail loudly if a direct physical DELETE or owner ID rekey would succeed.
        print("REAL_SCHEMA_RETENTION_RESULTS", backend, with_ubq_task_history, results)
        assert all(denied for denied, orphan in results.values()), (
            "P0 retained CAS owner could be orphaned by DELETE/rekey; "
            "actual schema / FK mode / UBQ-history result: "
            + repr((backend, with_ubq_task_history, results))
        )
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.connect() as c:
                await c.execute(text("DROP DATABASE IF EXISTS " + db_name + " WITH (FORCE)"))
            await admin.dispose()
