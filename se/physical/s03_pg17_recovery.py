"""Strict disposable PostgreSQL 17 template-copy crash and owner guard proof."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
S02 = "32a_alpha_31b_31t_merge"
S03 = "32b_alpha_s03_owner_integrity"
TEMPLATE = "s03_ci_recovery_template"
COPY = "s03_ci_recovery_copy"
WORKER = """
import asyncio,os,sys,asyncpg
async def run():
    conn=await asyncpg.connect(sys.argv[1])
    await conn.execute('BEGIN')
    await conn.execute("INSERT INTO users(id,email,password_hash,status) VALUES ('crash-user','crash@invalid','x','active')")
    await conn.execute("INSERT INTO file_blobs(id,storage_backend,object_key,state) VALUES ('crash-blob','local','crash-key','STAGING')")
    await conn.execute("INSERT INTO files(id,owner_user_id,blob_id,filename,mime_type,origin_type,state) VALUES ('crash-file','crash-user','crash-blob','x.txt','text/plain','USER_UPLOAD','STAGING')")
    os._exit(73)
asyncio.run(run())
"""


def _validate_env():
    dsn = os.environ["AIC_TEST_POSTGRES_URL"]
    u = make_url(dsn)
    assert (
        u.drivername, u.host, u.port, u.database, u.username, u.password
    ) == (
        "postgresql+asyncpg", "127.0.0.1", 5432, "s03_migration_ci", "s03_ci", None
    ), "Only strict CI-local PostgreSQL 17 may run this test"
    return dsn


def _config(monkeypatch, source_dsn, name):
    u = make_url(source_dsn).set(database=name)
    monkeypatch.setenv("ASSISTANT_ALEMBIC_DATABASE_URL", u.render_as_string(hide_password=False))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "se/src/infrastructure/storage/migrations/sql"))
    return cfg


async def _admin(source_dsn, name="postgres"):
    u = make_url(source_dsn).set(database=name)
    dsn = u.render_as_string(hide_password=False).replace("postgresql+asyncpg://", "postgresql://", 1)
    return await asyncpg.connect(dsn)


async def _counts(source_dsn, name):
    c = await _admin(source_dsn, name)
    try:
        ver = await c.fetchval("SELECT version_num FROM alembic_version")
        rows = [
            await c.fetchval("SELECT COUNT(*) FROM users WHERE id='crash-user'"),
            await c.fetchval("SELECT COUNT(*) FROM files WHERE id='crash-file'"),
            await c.fetchval("SELECT COUNT(*) FROM users WHERE id='original'"),
            await c.fetchval("SELECT COUNT(*) FROM files WHERE id='original-file' AND owner_user_id='original'"),
        ]
        return ver, rows
    finally:
        await c.close()


def test_s03_pg17_template_copy_crash_restart_and_forward_only(monkeypatch):
    source_dsn = _validate_env()

    async def initialize():
        admin = await _admin(source_dsn)
        try:
            version = int(await admin.fetchval("SHOW server_version_num"))
            assert 170000 <= version < 180000
            for name in (TEMPLATE, COPY):
                assert await admin.fetchval(
                    "SELECT COUNT(*) FROM pg_database WHERE datname=$1", name
                ) == 0, "Never overwrite existing database"
            await admin.execute("CREATE DATABASE " + TEMPLATE)
        finally:
            await admin.close()

    asyncio.run(initialize())
    try:
        command.upgrade(_config(monkeypatch, source_dsn, TEMPLATE), S02)

        async def seed():
            c = await _admin(source_dsn, TEMPLATE)
            try:
                await c.execute(
                    "INSERT INTO users(id,email,password_hash,status) VALUES "
                    "('original','original@invalid','x','active')"
                )
                await c.execute(
                    "INSERT INTO file_blobs(id,storage_backend,object_key,state) "
                    "VALUES ('original-blob','local','original-key','STAGING')"
                )
                await c.execute(
                    "INSERT INTO files(id,owner_user_id,blob_id,filename,mime_type,origin_type,state) "
                    "VALUES ('original-file','original','original-blob','x.txt','text/plain','USER_UPLOAD','STAGING')"
                )
            finally:
                await c.close()
        asyncio.run(seed())

        async def clone():
            admin = await _admin(source_dsn)
            try:
                await admin.execute(
                    "CREATE DATABASE " + COPY + " WITH TEMPLATE " + TEMPLATE
                )
            finally:
                await admin.close()
        asyncio.run(clone())

        cfg = _config(monkeypatch, source_dsn, COPY)
        command.upgrade(cfg, S03)
        command.upgrade(cfg, S03)  # idempotent repeated upgrade

        dsn = make_url(source_dsn).set(database=COPY).render_as_string(
            hide_password=False
        ).replace("postgresql+asyncpg://", "postgresql://", 1)
        child = subprocess.run(
            [sys.executable, "-c", WORKER, dsn], cwd=ROOT,
            capture_output=True, text=True, timeout=50,
        )
        assert child.returncode == 73, (child.returncode, child.stderr[-1500:])
        assert asyncio.run(_counts(source_dsn, TEMPLATE)) == (
            S02, [0, 0, 1, 1]
        )
        assert asyncio.run(_counts(source_dsn, COPY)) == (
            S03, [0, 0, 1, 1]
        )

        async def deny():
            c = await _admin(source_dsn, COPY)
            try:
                for sql in (
                    "DELETE FROM users WHERE id='original'",
                    "UPDATE users SET id='intruder' WHERE id='original'",
                    "UPDATE files SET owner_user_id='intruder' WHERE id='original-file'",
                ):
                    tx = c.transaction()
                    await tx.start()
                    with pytest.raises(asyncpg.PostgresError):
                        await c.execute(sql)
                    await tx.rollback()
            finally:
                await c.close()
        asyncio.run(deny())

        with pytest.raises(RuntimeError, match="S03_OWNER_INTEGRITY_FORWARD_ONLY"):
            command.downgrade(cfg, S02)
        assert asyncio.run(_counts(source_dsn, COPY)) == (
            S03, [0, 0, 1, 1]
        )
    finally:
        async def cleanup():
            admin = await _admin(source_dsn)
            try:
                for name in (COPY, TEMPLATE):
                    await admin.execute(
                        "DROP DATABASE IF EXISTS " + name + " WITH (FORCE)"
                    )
            finally:
                await admin.close()
        asyncio.run(cleanup())
