"""Real PostgreSQL17 Alembic CLI composition evidence for PR494 + PR496 + PR500.

These tests use disposable physical databases and separate Python/Alembic
processes. No alembic stamp, fake revision table, synthetic migration body,
test-side version-width ALTER, or SQLite fallback is permitted.
"""
import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.exc import DBAPIError


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = Path("se/src/infrastructure/storage/migrations/sql")
REV31A = "31a_aic0_agent_instance_identity"
REV31B = "31b_aic0_pg_guard_forward"
ORIGINAL_HEAD = "6edd86a050cb925939c40468e710d286abaaff41"
ORIGINAL_BLOB = "3cb7528e622a26a62f2d4010986bfdd47df17873"


def _database_url():
    url = os.environ.get("AIC_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Real PostgreSQL 17 AIC_TEST_POSTGRES_URL is mandatory")
    return url


def _alembic(url, revision, *, config=ROOT / "alembic.ini"):
    env = dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(config), "upgrade", revision],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=240,
        check=False,
    )
    assert result.returncode == 0, (
        f"Physical Alembic upgrade {revision} failed: {result.returncode}\n"
        f"STDOUT:\n{result.stdout[-4000:]}\nSTDERR:\n{result.stderr[-8000:]}"
    )


def _old31a_script_config(tmp_path):
    """Use authentic old 31a migration for Alembic CLI, not only DDL replay."""
    old = subprocess.run(
        ["git", "show", f"{ORIGINAL_HEAD}:{SCRIPT_PATH.as_posix()}/{REV31A}.py"],
        cwd=ROOT, capture_output=True, check=True, timeout=30,
    ).stdout
    blob_hash = hashlib.sha1(
        b"blob " + str(len(old)).encode() + b"\0" + old
    ).hexdigest()
    assert blob_hash == ORIGINAL_BLOB, "Immutable historical old-31a blob mismatch"

    old_dir = tmp_path / "alembic_old31a"
    shutil.copytree(ROOT / SCRIPT_PATH, old_dir)
    (old_dir / "versions" / f"{REV31A}.py").write_bytes(old)
    ini = (ROOT / "alembic.ini").read_text(encoding="utf-8")
    baseline = "script_location = se/src/infrastructure/storage/migrations/sql"
    assert ini.count(baseline) == 1, "Unexpected Alembic script_location"
    alternate = ini.replace(baseline, "script_location = " + old_dir.as_posix())
    conf = tmp_path / "alembic_old31a.ini"
    conf.write_text(alternate, encoding="utf-8")
    return conf


async def _new_database():
    parent = _database_url()
    admin = create_async_engine(parent, isolation_level="AUTOCOMMIT")
    name = "aic_composed_" + uuid4().hex[:15]
    async with admin.connect() as conn:
        version = int((await conn.execute(text("SHOW server_version_num"))).scalar_one())
        assert 170000 <= version < 180000, "Physical PG17 required"
        await conn.execute(text("CREATE DATABASE " + name))
    url = make_url(parent).set(database=name).render_as_string(hide_password=False)
    engine = create_async_engine(url, pool_pre_ping=True)
    return admin, engine, name, url


async def _drop_database(admin, engine, name):
    await engine.dispose()
    async with admin.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS " + name + " WITH (FORCE)"))
    await admin.dispose()


async def _revision(engine):
    await engine.dispose()
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()


async def _scalar(engine, sql, params=None):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).scalar_one()


async def _retire_raw(engine, identity):
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO aic_instance_reservations "
            "(agent_instance_id,state,generation) VALUES (:i,'ACTIVE',1)"
        ), {"i": identity})
        await conn.execute(text(
            "INSERT INTO aic_instance_live "
            "(agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) VALUES (:i,'alice','definition','v1','ACTIVE',1)"
        ), {"i": identity})
        await conn.execute(text(
            "UPDATE aic_instance_reservations SET state='RETIRING' "
            "WHERE agent_instance_id=:i"
        ), {"i": identity})
        await conn.execute(text(
            "DELETE FROM aic_instance_live WHERE agent_instance_id=:i"
        ), {"i": identity})
        await conn.execute(text(
            "UPDATE aic_instance_reservations SET state='RETIRED',generation=2 "
            "WHERE agent_instance_id=:i"
        ), {"i": identity})


async def _assert_denied_after_repair(url, identity):
    worker = r"""
import asyncio, asyncpg, os, sys
async def main():
    dsn, identity = sys.argv[1:]
    c = await asyncpg.connect(dsn=dsn)
    await c.execute("CREATE TEMP TABLE users (id TEXT, status TEXT)")
    await c.execute("CREATE TEMP TABLE aic_instance_reservations (agent_instance_id TEXT, state TEXT)")
    await c.execute("INSERT INTO pg_temp.users VALUES ('alice','active')")
    await c.execute("INSERT INTO pg_temp.aic_instance_reservations VALUES ($1,'ACTIVE')", identity)
    await c.execute("SET search_path TO pg_temp, public")
    try:
        await c.execute(
            "INSERT INTO public.aic_instance_live "
            "(agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) VALUES ($1,'alice','forged','v1','ACTIVE',1)",
            identity,
        )
    except Exception as e:
        if "AIC_LIVE_OWNER_OR_ID_INVALID" not in str(e):
            print("UNEXPECTED_DENIAL:" + str(e), flush=True)
            os._exit(19)
        print("AIC31B_CLI_REPAIR_DENIED", flush=True)
        os._exit(77)
    print("AIC31B_SECURITY_BYPASS", flush=True)
    os._exit(20)
asyncio.run(main())
"""
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    result = subprocess.run(
        [sys.executable, "-c", worker, dsn, identity],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 77, (
        result.returncode, result.stdout, result.stderr
    )
    assert "AIC31B_CLI_REPAIR_DENIED" in result.stdout


@pytest.mark.asyncio
async def test_composed_pg17_cli_old_stamped_31a_to_31b(tmp_path):
    """Real old-31a Alembic CLI -> restart -> NEW Alembic CLI 31b."""
    admin, engine, name, url = await _new_database()
    identity = "retired-" + uuid4().hex
    try:
        old_ini = _old31a_script_config(tmp_path)
        _alembic(url, REV31A, config=old_ini)
        assert await _revision(engine) == REV31A
        for function in ("aic_id_guard", "aic_live_guard", "aic_user_guard"):
            assert (await _scalar(engine, """
                SELECT p.proconfig FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
                WHERE ns.nspname='public' AND p.proname=:fn
                  AND p.pronargs=0
            """, {"fn": function})) is None
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice@aic-composed.invalid','x','active')"
            ))
        await _retire_raw(engine, identity)
        # Confirm original installed 31a is vulnerable without committing exploit.
        async with engine.connect() as conn:
            await conn.execute(text("CREATE TEMP TABLE users (id TEXT, status TEXT)"))
            await conn.execute(text(
                "CREATE TEMP TABLE aic_instance_reservations "
                "(agent_instance_id TEXT, state TEXT)"
            ))
            await conn.execute(text(
                "INSERT INTO pg_temp.users VALUES ('alice','active')"
            ))
            await conn.execute(text(
                "INSERT INTO pg_temp.aic_instance_reservations "
                "VALUES (:i,'ACTIVE')"
            ), {"i": identity})
            await conn.execute(text("SET LOCAL search_path TO pg_temp, public"))
            await conn.execute(text(
                "INSERT INTO public.aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,"
                "definition_version,lifecycle_state,generation) "
                "VALUES (:i,'alice','forged','v1','ACTIVE',1)"
            ), {"i": identity})
            assert (await conn.execute(text(
                "SELECT COUNT(*) FROM public.aic_instance_live "
                "WHERE agent_instance_id=:i"
            ), {"i": identity})).scalar_one() == 1
            await conn.rollback()
        assert await _scalar(engine,
            "SELECT COUNT(*) FROM aic_instance_live WHERE agent_instance_id=:i",
            {"i": identity}) == 0

        # Same durable DB, real brand-new Alembic CLI using composed code.
        _alembic(url, REV31B)
        assert await _revision(engine) == REV31B
        for function in ("aic_id_guard", "aic_live_guard", "aic_user_guard"):
            config = await _scalar(engine, """
                SELECT p.proconfig FROM pg_catalog.pg_proc p
                JOIN pg_catalog.pg_namespace ns ON ns.oid=p.pronamespace
                WHERE ns.nspname='public' AND p.proname=:fn
                  AND p.pronargs=0
            """, {"fn": function})
            assert config and len(config) == 1
            assert config[0].startswith("search_path=pg_catalog,")
            assert config[0].endswith(", pg_temp")
        async with engine.connect() as conn:
            rows = (await conn.execute(text("""
                SELECT t.tgname, rel.relname, p.proname, t.tgtype,
                       t.tgenabled::text
                FROM pg_catalog.pg_trigger t
                JOIN pg_catalog.pg_class rel ON rel.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace ns ON ns.oid=rel.relnamespace
                JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                WHERE ns.nspname='public' AND t.tgname LIKE 'trg_aic_%'
            """))).all()
            assert len(rows) == 4 and all(row[4] == "O" for row in rows), rows
        await _assert_denied_after_repair(url, identity)
        assert await _scalar(engine,
            "SELECT state FROM aic_instance_reservations WHERE agent_instance_id=:i",
            {"i": identity}) == "RETIRED"
        assert await _scalar(engine,
            "SELECT COUNT(*) FROM aic_instance_live WHERE agent_instance_id=:i",
            {"i": identity}) == 0
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                await conn.execute(text(
                    "UPDATE aic_instance_reservations SET state='ACTIVE' "
                    "WHERE agent_instance_id=:i"
                ), {"i": identity})
            await conn.rollback()
    finally:
        await _drop_database(admin, engine, name)


@pytest.mark.asyncio
async def test_composed_pg17_cli_pristine_ubq_history_to_31b():
    """No stamp: true PG17 base -> 24a + 25a + 26a + 30a -> 31b."""
    admin, engine, name, url = await _new_database()
    task, policy, receipt = (
        "legacy-task-" + name, "legacy-policy-" + name, "legacy-receipt-" + name
    )
    try:
        _alembic(url, "24a_r12_stale_lease_scan_index")
        assert await _revision(engine) == "24a_r12_stale_lease_scan_index"
        assert await _scalar(engine, """
            SELECT character_maximum_length FROM information_schema.columns
            WHERE table_schema='public' AND table_name='alembic_version'
              AND column_name='version_num'
        """) >= 255, "Real #494 preflight did not widen existing version metadata"
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice@aic-history.invalid','x','active')"
            ))
            await conn.execute(text(
                "INSERT INTO agent_tasks "
                "(id,session_id,created_by,assigned_agent_id,status,wait_reasons,input) "
                "VALUES (:task,'historical-session','alice','legacy-agent',"
                "'CREATED','[]','{}')"
            ), {"task": task})
            await conn.execute(text(
                "INSERT INTO agent_task_budgets "
                "(task_id,max_total_executions,max_active_executions,"
                "max_active_branches,max_parallel_agents,max_total_tool_calls,"
                "max_total_inference_calls,max_delegation_depth,"
                "policy_version,policy_fingerprint) "
                "VALUES (:task,10,3,3,3,10,10,3,'legacy-v1',:fp)"
            ), {"task": task, "fp": "b"*64})
            await conn.execute(text(
                "INSERT INTO agent_task_budget_reservations "
                "(task_id,kind,reservation_key,payload_fingerprint) "
                "VALUES (:task,'USAGE','legacy-usage',:fp)"
            ), {"task": task, "fp": "r"*64})
        _alembic(url, "25a_ubq1_user_budget_foundation")
        assert await _revision(engine) == "25a_ubq1_user_budget_foundation"
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO user_budget_policies "
                "(policy_id,owner_user_id,policy_version,policy_fingerprint,"
                "window_duration_seconds,tool_limits_json) "
                "VALUES (:policy,'alice','legacy-v1',:fp,3600,'{}')"
            ), {"policy": policy, "fp": "a"*64})
            await conn.execute(text(
                "INSERT INTO user_budget_accounts(owner_user_id,next_window_epoch) "
                "VALUES ('alice',1)"
            ))
        _alembic(url, "26a_ubq2_dual_accounting_bridge")
        assert await _revision(engine) == "26a_ubq2_dual_accounting_bridge"
        assert await _scalar(engine,
            "SELECT incarnation_generation FROM agent_task_budgets "
            "WHERE task_id=:task", {"task": task}) == 1
        async with engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO user_budget_task_bindings "
                "(task_id,owner_user_id,enrollment_version,source_auth_type,"
                "resolution_fingerprint) VALUES (:task,'alice','ubq2-v1','jwt',:fp)"
            ), {"task": task, "fp": "c"*64})
            await conn.execute(text(
                "INSERT INTO user_budget_dual_accounting_receipts "
                "(bridge_receipt_id,owner_user_id,task_id,task_budget_kind,"
                "task_budget_reservation_key,mirror_dimension,"
                "source_payload_fingerprint,amount_atomic) "
                "VALUES (:receipt,'alice',:task,'USAGE','legacy-usage',"
                "'NO_CHARGE',:fp,0)"
            ), {"receipt": receipt, "task": task, "fp": "r"*64})
        _alembic(url, "30a_ctx_f5_user_wide_memory_scope")
        assert await _revision(engine) == "30a_ctx_f5_user_wide_memory_scope"
        _alembic(url, REV31B)
        assert await _revision(engine) == REV31B
        assert await _scalar(engine,
            "SELECT COUNT(*) FROM user_budget_policies WHERE policy_id=:policy",
            {"policy": policy}) == 1
        assert await _scalar(engine,
            "SELECT COUNT(*) FROM user_budget_dual_accounting_receipts "
            "WHERE bridge_receipt_id=:receipt", {"receipt": receipt}) == 1
        assert await _scalar(engine,
            "SELECT next_generation FROM agent_task_budget_incarnation_allocator "
            "WHERE allocator_id=1") == 2
        identity = "reserved-" + uuid4().hex
        await _retire_raw(engine, identity)
        await _assert_denied_after_repair(url, identity)
        assert await _scalar(engine,
            "SELECT state FROM aic_instance_reservations WHERE agent_instance_id=:i",
            {"i": identity}) == "RETIRED"
    finally:
        await _drop_database(admin, engine, name)
