"""S02 PG17 copy-only recovery: transactional abort, restart and merge rollback.

Run explicitly inside S02's ephemeral localhost PG17 Actions service.
All created databases are S02-owned and must not already exist.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from s02_pg17_physical import _dsn_guard, _conn_params

ROOT = Path(__file__).resolve().parents[2]
AIC = "31b_aic0_pg_guard_forward"
TERMINAL = "31t_direct_terminal_permission"
MERGE = "32a_alpha_31b_31t_merge"
CASES = {"restart": "s02_ci_recovery", "rollback": "s02_ci_rollback"}

_WORKER = r"""
import os
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "after_cursor_execute")
def interrupt(conn, cursor, statement, parameters, context, executemany):
    if "INSERT INTO permissions" in statement and "terminal:execute" in str(parameters):
        os._exit(77)

root = Path(os.environ["S02_REPO_ROOT"])
cfg = Config(str(root / "alembic.ini"))
cfg.set_main_option("script_location", str(root / "se/src/infrastructure/storage/migrations/sql"))
command.upgrade(cfg, "32a_alpha_31b_31t_merge")
raise SystemExit(99)
"""


def _config(monkeypatch, dbname):
    monkeypatch.setenv(
        "ASSISTANT_ALEMBIC_DATABASE_URL",
        f"postgresql+asyncpg://s02_ci@127.0.0.1:5432/{dbname}",
    )
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(ROOT / "se/src/infrastructure/storage/migrations/sql"),
    )
    return cfg


async def _create_from_template(dbname):
    conn = await asyncpg.connect(**_conn_params("postgres"))
    try:
        version = int(await conn.fetchval("SHOW server_version_num"))
        assert 170000 <= version < 180000
        assert dbname in CASES.values(), "Only frozen S02 DB identifiers allowed"
        assert await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1", dbname
        ) is None, "Refuse existing DB; this test never overwrites an existing DB"
        # The existing dedicated DB was seeded and its writer connection closed.
        assert await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname='s02_migration_ci'"
        ) == 1
        await conn.execute(
            f"CREATE DATABASE {dbname} WITH TEMPLATE s02_migration_ci OWNER s02_ci"
        )
    finally:
        await conn.close()


async def _versions(db):
    conn = await asyncpg.connect(**_conn_params(db))
    try:
        return {v[0] for v in await conn.fetch("SELECT version_num FROM alembic_version")}
    finally:
        await conn.close()


async def _count(db, sql):
    conn = await asyncpg.connect(**_conn_params(db))
    try:
        return await conn.fetchval(sql)
    finally:
        await conn.close()


def _verify(db, grants, permission):
    assert asyncio.run(_count(db, "SELECT count(*) FROM role_permissions")) == grants
    assert asyncio.run(_count(db, "SELECT count(*) FROM users WHERE id='s02-copy-owner'")) == 1
    assert asyncio.run(_count(
        db, "SELECT count(*) FROM permissions WHERE name='terminal:execute'"
    )) == permission
    assert asyncio.run(_count(
        db, "SELECT count(*) FROM role_permissions rp JOIN permissions p "
        "ON p.id=rp.permission_id WHERE p.name='terminal:execute'"
    )) == 0


@pytest.mark.parametrize("case", ("restart", "rollback"))
def test_s02_pg17_copy_recovery_without_role_grants(monkeypatch, case):
    _dsn_guard()  # strict PG17, loopback, exact temporary user, no password
    db = CASES[case]
    main = _config(monkeypatch, "s02_migration_ci")
    # Seed dedicated template DB only once per job; test cases each get a clone.
    if case == "restart":
        command.upgrade(main, AIC)
        async def seed():
            conn = await asyncpg.connect(**_conn_params("s02_migration_ci"))
            try:
                await conn.execute(
                    "INSERT INTO users(id,email,password_hash,status) "
                    "VALUES ('s02-copy-owner','s02-copy-owner@example.invalid','x','active')"
                )
                return await conn.fetchval("SELECT count(*) FROM role_permissions")
            finally:
                await conn.close()
        grants = asyncio.run(seed())
    else:
        grants = asyncio.run(_count("s02_migration_ci", "SELECT count(*) FROM role_permissions"))

    asyncio.run(_create_from_template(db))
    cfg = _config(monkeypatch, db)
    assert asyncio.run(_versions(db)) == {AIC}

    if case == "restart":
        env = os.environ.copy()
        env["S02_REPO_ROOT"] = str(ROOT)
        child = subprocess.run(
            [sys.executable, "-c", _WORKER], env=env, cwd=ROOT,
            text=True, capture_output=True, timeout=120,
        )
        assert child.returncode == 77, (
            f"Expected abrupt process death during INSERT; got {child.returncode}: "
            f"{child.stderr[-1800:]}"
        )
        assert asyncio.run(_versions(db)) == {AIC}
        _verify(db, grants, permission=0)
        assert asyncio.run(_versions("s02_migration_ci")) == {AIC}
        command.upgrade(cfg, "32a_alpha_31b_31t_merge")
        command.upgrade(cfg, "32a_alpha_31b_31t_merge")
        assert asyncio.run(_versions(db)) == {MERGE}
        _verify(db, grants, permission=1)
    else:
        command.upgrade(cfg, "32a_alpha_31b_31t_merge")
        assert asyncio.run(_versions(db)) == {MERGE}
        _verify(db, grants, permission=1)
        # Revision 31b strictly forbids downgrade: only merge-marker rollback.
        # A relative -1 from merge 32a is ambiguous across 31b/31t.
        # An explicit 31b ancestor drops ONLY 32a, restoring both parent heads.
        # It must never downgrade 31b's security guard or the 31t permission.
        command.downgrade(cfg, AIC)
        assert asyncio.run(_versions(db)) == {AIC, TERMINAL}
        _verify(db, grants, permission=1)
        command.upgrade(cfg, "32a_alpha_31b_31t_merge")
        assert asyncio.run(_versions(db)) == {MERGE}
        _verify(db, grants, permission=1)
