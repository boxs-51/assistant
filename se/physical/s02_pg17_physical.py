"""S02 strict real PostgreSQL 17 migration probe (explicit CI invocation only).

NOT included in pytest's default test_*.py discovery. Its opt-in CI workflow
owns an ephemeral localhost PostgreSQL service, no external or production DSN.
"""
import asyncio
import os
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
MERGE = "32a_alpha_31b_31t_merge"
STARTS = {
    "base": ("30a_ctx_f5_user_wide_memory_scope", "s02_ci_base"),
    "aic": ("31b_aic0_pg_guard_forward", "s02_ci_aic"),
    "terminal": ("31t_direct_terminal_permission", "s02_ci_terminal"),
}


def _dsn_guard():
    if os.environ.get("ALPHA_S02_PG17_STRICT") != "1":
        pytest.fail("S02 strict disposable PostgreSQL 17 required, no skip")
    source = os.environ.get("ALPHA_S02_PG17_URL", "")
    try:
        url = make_url(source)
    except Exception:
        pytest.fail("S02 disposable PostgreSQL DSN missing/invalid")
    if (url.drivername != "postgresql+asyncpg"
        or url.host != "127.0.0.1" or url.port != 5432
        or url.username != "s02_ci" or url.password is not None
        or url.database != "s02_migration_ci"):
        pytest.fail("Only explicit loopback S02 ephemeral PostgreSQL is permitted")
    return url


def _conn_params(dbname):
    return dict(host="127.0.0.1", port=5432, user="s02_ci", database=dbname)


async def _create_disposable_db(dbname):
    conn = await asyncpg.connect(**_conn_params("postgres"))
    try:
        version = int(await conn.fetchval("SHOW server_version_num"))
        assert 170000 <= version < 180000, "Only real PostgreSQL 17 is admissible"
        present = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1", dbname
        )
        assert present is None, "Existing database: refuse destructive reuse"
        # Whitelisted static names from STARTS; no user-provided SQL identifier.
        assert dbname in {x[1] for x in STARTS.values()}
        await conn.execute(f"CREATE DATABASE {dbname} OWNER s02_ci")
    finally:
        await conn.close()


@pytest.mark.parametrize("case", ("base", "aic", "terminal"))
def test_s02_pg17_merges_both_populated_histories_without_privilege_gain(
    monkeypatch, case,
):
    _dsn_guard()
    start, dbname = STARTS[case]
    asyncio.run(_create_disposable_db(dbname))
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"postgresql+asyncpg://s02_ci@127.0.0.1:5432/{dbname}",
    )
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location",
        str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    command.upgrade(cfg, start)

    async def seed():
        conn = await asyncpg.connect(**_conn_params(dbname))
        try:
            await conn.execute(
                "INSERT INTO users (id,email,password_hash,status) "
                "VALUES ('s02-pg-owner','s02-pg-owner@example.invalid','x','active')"
            )
            return await conn.fetchval("SELECT count(*) FROM role_permissions")
        finally:
            await conn.close()

    prior_grants = asyncio.run(seed())
    command.upgrade(cfg, MERGE)
    command.upgrade(cfg, MERGE)

    async def check():
        conn = await asyncpg.connect(**_conn_params(dbname))
        try:
            assert await conn.fetch("SELECT version_num FROM alembic_version")
            assert await conn.fetchval("SELECT version_num FROM alembic_version") == MERGE
            assert await conn.fetchval("SELECT count(*) FROM alembic_version") == 1
            assert await conn.fetchval("SELECT count(*) FROM users WHERE id='s02-pg-owner'") == 1
            assert await conn.fetchval(
                "SELECT count(*) FROM permissions WHERE name='terminal:execute'"
            ) == 1
            assert await conn.fetchval("SELECT count(*) FROM role_permissions") == prior_grants
            assert await conn.fetchval(
                "SELECT count(*) FROM role_permissions rp JOIN permissions p "
                "ON p.id=rp.permission_id WHERE p.name='terminal:execute'"
            ) == 0
            assert await conn.fetchval(
                "SELECT count(*) FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public' AND p.proname IN "
                "('aic_id_guard','aic_live_guard','aic_user_guard') "
                "AND array_to_string(p.proconfig, ',') LIKE 'search_path=pg_catalog,%pg_temp'"
            ) == 3
            await conn.execute(
                "INSERT INTO aic_instance_reservations(agent_instance_id,state,generation) "
                "VALUES ('s02-pg-live','ACTIVE',1)"
            )
            await conn.execute(
                "INSERT INTO aic_instance_live("
                "agent_instance_id,owner_user_id,definition_id,definition_version,"
                "lifecycle_state,generation) "
                "VALUES ('s02-pg-live','s02-pg-owner','s02-def','v1','ACTIVE',1)"
            )
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("DELETE FROM users WHERE id='s02-pg-owner'")
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("UPDATE users SET id='s02-other' WHERE id='s02-pg-owner'")
            assert await conn.fetchval(
                "SELECT owner_user_id FROM aic_instance_live WHERE agent_instance_id='s02-pg-live'"
            ) == "s02-pg-owner"
        finally:
            await conn.close()

    asyncio.run(check())
