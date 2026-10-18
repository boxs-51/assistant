"""AIC-0-P1A genuine SQLite FK-OFF and optional native PostgreSQL tests.

No fake UoW/session, no hidden second commit, and no runtime/API activation.
Set AIC_TEST_POSTGRES_URL to a disposable PostgreSQL DB to run the PG lock test.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DatabaseError, DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.domain.schemas.identity import Identity
from se.src.application.agent_instance import AgentInstanceService
from se.src.infrastructure.storage.models.sql.agent.agent_instance import (
    AgentInstanceLiveRecord,
    AgentInstanceReservationRecord,
)
from se.src.infrastructure.storage.models.sql.user_data.user import User
from se.src.infrastructure.storage.repositories.agent_instance import (
    AgentInstanceDenied,
    AgentInstanceRepository,
)

MIG = importlib.import_module(
    "se.src.infrastructure.storage.migrations.sql.versions.31a_aic0_agent_instance_identity"
)


class _Uow:
    def __init__(self, session):
        self.session = session
        self.agent_instances = AgentInstanceRepository(session)


async def _tables(engine, *, dialect: str):
    async with engine.begin() as conn:
        if dialect == "sqlite":
            await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        await conn.run_sync(User.__table__.create)
        if dialect == "postgresql":
            # Execute the actual Alembic upgrade with PostgreSQL DDL and PL/pgSQL.
            await conn.run_sync(lambda sync: _run_aic_migration(sync, direction="upgrade"))
        else:
            await conn.run_sync(AgentInstanceReservationRecord.__table__.create)
            await conn.run_sync(AgentInstanceLiveRecord.__table__.create)
            for sql in MIG._SQLITE_GUARDS:
                await conn.execute(text(sql))


async def _user(engine, uid="alice", status="active"):
    async with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        await conn.execute(
            text("INSERT INTO users(id,email,password_hash,status) VALUES (:i,:e,'x',:s)"),
            {"i": uid, "e": uid + "@aic.invalid", "s": status},
        )


async def _create(sessions, uid="alice"):
    async with sessions() as session:
        row = await AgentInstanceRepository(session).create(uid, "definition", "v1")
        instance_id = row.agent_instance_id
        await session.commit()
        return instance_id


@pytest_asyncio.fixture
async def sqlite_aic(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + str(tmp_path / "aic426.sqlite"),
        connect_args={"timeout": 0.08},
    )
    try:
        await _tables(engine, dialect="sqlite")
        await _user(engine)
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_aic_sqlite_fk_off_raw_delete_live_and_retired(sqlite_aic):
    engine, sessions = sqlite_aic
    instance_id = await _create(sessions)
    async with engine.connect() as c1, engine.connect() as c2:
        for c in (c1, c2):
            await c.exec_driver_sql("PRAGMA foreign_keys=OFF")
            assert (await c.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 0
        with pytest.raises(DatabaseError, match="AIC_LIVE_OWNER_DELETE_DENIED"):
            await c2.exec_driver_sql("DELETE FROM users WHERE id='alice'")
        await c2.rollback()
        # Lifecycle SUSPENDED still keeps a LIVE ownership link and guard.
        async with sessions() as s:
            assert await AgentInstanceRepository(s).suspend("alice", instance_id, 1) == 2
            await s.commit()
        with pytest.raises(DatabaseError, match="AIC_LIVE_OWNER_DELETE_DENIED"):
            await c1.exec_driver_sql("DELETE FROM users WHERE id='alice'")
        await c1.rollback()
        async with sessions() as s:
            assert await AgentInstanceRepository(s).retire("alice", instance_id, 2) == 3
            await s.commit()
        assert (await c2.exec_driver_sql("DELETE FROM users WHERE id='alice'")).rowcount == 1
        await c2.commit()
        assert (await c1.exec_driver_sql(
            "SELECT state FROM aic_instance_reservations WHERE agent_instance_id=?",
            (instance_id,),
        )).scalar_one() == "RETIRED"
        with pytest.raises(DatabaseError, match="AIC_ID_REUSE_FORBIDDEN"):
            await c1.exec_driver_sql(
                "DELETE FROM aic_instance_reservations WHERE agent_instance_id=?",
                (instance_id,),
            )
        await c1.rollback()


@pytest.mark.asyncio
async def test_aic_sqlite_fk_off_owner_status_and_direct_sql_integrity(sqlite_aic):
    engine, sessions = sqlite_aic
    instance_id = await _create(sessions)
    async with engine.begin() as c:
        await c.exec_driver_sql("PRAGMA foreign_keys=OFF")
        await c.execute(text("UPDATE users SET status='inactive' WHERE id='alice'"))
    async with sessions() as s:
        with pytest.raises(AgentInstanceDenied):
            await AgentInstanceRepository(s).resolve("alice", instance_id)
        await s.rollback()
    async with sessions() as s:
        with pytest.raises(AgentInstanceDenied):
            await AgentInstanceRepository(s).create("alice", "definition", "v1")
        await s.rollback()
    async with engine.connect() as c:
        await c.exec_driver_sql("PRAGMA foreign_keys=OFF")
        with pytest.raises(DatabaseError, match="AIC_LIVE_OWNER_OR_ID_INVALID"):
            await c.execute(text(
                "INSERT INTO aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,definition_version,"
                "lifecycle_state,generation) "
                "VALUES ('forged','missing','d','v','ACTIVE',1)"
            ))
        await c.rollback()
        with pytest.raises(DatabaseError, match="AIC_LIVE_UPDATE_FORBIDDEN"):
            await c.execute(text(
                "UPDATE aic_instance_live SET owner_user_id='other' "
                "WHERE agent_instance_id=:id"
            ), {"id": instance_id})
        await c.rollback()
        with pytest.raises(DatabaseError, match="AIC_LIVE_DELETE_FORBIDDEN"):
            await c.execute(
                text("DELETE FROM aic_instance_live WHERE agent_instance_id=:id"),
                {"id": instance_id},
            )
        await c.rollback()


@pytest.mark.asyncio
async def test_aic_sqlite_generation_cross_owner_and_replay(sqlite_aic):
    engine, sessions = sqlite_aic
    await _user(engine, "bob")
    instance_id = await _create(sessions)
    identity = Identity(user_id="alice", auth_type="jwt")
    for other in (
        Identity(user_id="bob", auth_type="jwt"),
        Identity(user_id=None, auth_type="guest"),
        Identity(user_id="alice", auth_type="api_key"),
        Identity(user_id="alice", auth_type="admin_key"),
    ):
        async with sessions() as s:
            with pytest.raises(AgentInstanceDenied):
                await AgentInstanceService(_Uow(s)).resolve(other, instance_id)
            await s.rollback()
    async with sessions() as s:
        service = AgentInstanceService(_Uow(s))
        row = await service.resolve(identity, instance_id)
        assert row.generation == 1
        await s.rollback()
    async with sessions() as s:
        service = AgentInstanceService(_Uow(s))
        assert await service.suspend(identity, instance_id, expected_generation=1) == 2
        await s.commit()
    async with sessions() as s:
        with pytest.raises(AgentInstanceDenied):
            await AgentInstanceService(_Uow(s)).resolve(identity, instance_id)
        await s.rollback()
    async with sessions() as s:
        with pytest.raises(AgentInstanceDenied):
            await AgentInstanceService(_Uow(s)).retire(
                identity, instance_id, expected_generation=1
            )
        await s.rollback()


@pytest.mark.asyncio
async def test_aic_sqlite_first_statement_busy_and_rollback(sqlite_aic):
    engine, sessions = sqlite_aic
    async with sessions() as early:
        await early.execute(text("SELECT status FROM users WHERE id='alice'"))
        with pytest.raises(AgentInstanceDenied, match="fresh caller"):
            await AgentInstanceRepository(early).create("alice", "d", "v")
        await early.rollback()
    async with engine.connect() as writer:
        await writer.exec_driver_sql("BEGIN IMMEDIATE")
        async with sessions() as blocked:
            with pytest.raises(AgentInstanceDenied, match="write-intent"):
                await AgentInstanceRepository(blocked).create("alice", "d", "v")
            await blocked.rollback()
        await writer.commit()
    async with sessions() as session:
        row = await AgentInstanceRepository(session).create("alice", "d", "v")
        rolled_id = row.agent_instance_id
        await session.rollback()
    async with engine.connect() as conn:
        assert (await conn.execute(
            text("SELECT COUNT(*) FROM aic_instance_reservations "
                 "WHERE agent_instance_id=:id"), {"id": rolled_id}
        )).scalar_one() == 0


@pytest.mark.asyncio
async def test_aic_sqlite_create_status_both_commit_orders(sqlite_aic):
    engine, sessions = sqlite_aic
    instance_id = await _create(sessions)
    async with engine.begin() as c:
        await c.execute(text("UPDATE users SET status='inactive' WHERE id='alice'"))
    async with sessions() as s:
        with pytest.raises(AgentInstanceDenied):
            await AgentInstanceRepository(s).resolve("alice", instance_id)
        await s.rollback()
    async with sessions() as s:
        with pytest.raises(AgentInstanceDenied):
            await AgentInstanceRepository(s).create("alice", "d", "v")
        await s.rollback()
    # Re-activation permits a *fresh* resolve of still-live ACTIVE only.
    async with engine.begin() as c:
        await c.execute(text("UPDATE users SET status='active' WHERE id='alice'"))
    async with sessions() as s:
        assert (await AgentInstanceRepository(s).resolve(
            "alice", instance_id
        )).agent_instance_id == instance_id
        await s.rollback()


@pytest.fixture
def postgres_url(tmp_path):
    """Use caller's genuine PG or provision a disposable native server on GitHub Linux."""
    external = os.getenv("AIC_TEST_POSTGRES_URL")
    if external:
        yield external
        return
    if os.getenv("GITHUB_ACTIONS") != "true" or sys.platform == "win32":
        pytest.skip("No AIC_TEST_POSTGRES_URL and no supported native PG test runner")
    candidates = []
    if shutil.which("pg_config"):
        config = subprocess.run(
            ["pg_config", "--bindir"], capture_output=True, text=True, check=False
        )
        if config.returncode == 0:
            candidates.append(Path(config.stdout.strip()))
    candidates.extend(sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True))
    binaries = next(
        (b for b in candidates if (b / "initdb").is_file() and (b / "pg_ctl").is_file()),
        None,
    )
    if binaries is None:
        pytest.skip("Native PostgreSQL initdb/pg_ctl unavailable on this runner")
    data = tmp_path / "nativepg"
    log = tmp_path / "nativepg.log"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    init = subprocess.run(
        [str(binaries / "initdb"), "-D", str(data), "-U", "postgres",
         "--auth-local=trust", "--auth-host=trust", "--no-sync"],
        capture_output=True, text=True, timeout=40, check=False,
    )
    assert init.returncode == 0, "Native PG initdb failed: " + init.stderr[-2500:]
    start = subprocess.run(
        [str(binaries / "pg_ctl"), "-D", str(data), "-l", str(log),
         "-o", f"-F -h 127.0.0.1 -k {tmp_path} -p {port}", "-w", "start"],
        capture_output=True, text=True, timeout=40, check=False,
    )
    assert start.returncode == 0, (
        "Native PG startup failed: " + start.stderr[-1000:] + " " +
        (log.read_text(errors="replace")[-2000:] if log.exists() else "")
    )
    try:
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate",
             "-w", "stop"],
            capture_output=True, text=True, timeout=30, check=False,
        )


@pytest.mark.asyncio
async def test_aic_native_postgres_nonkey_status_update_lock(postgres_url):
    # Real two-session row lock; no fake dialect or SQLite-as-PG substitution.
    url = postgres_url
    schema = "aic426_" + uuid4().hex[:12]
    admin = create_async_engine(url)
    async with admin.begin() as c:
        await c.execute(text("CREATE SCHEMA " + schema))
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        await _tables(engine, dialect="postgresql")
        await _user(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as creator, sessions() as updater:
            # AIC CREATE takes FOR SHARE on users; a normal status UPDATE conflicts.
            await AgentInstanceRepository(creator).create("alice", "d", "v")
            await updater.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(DBAPIError) as locked:
                await updater.execute(
                    text("UPDATE users SET status='inactive' WHERE id='alice'")
                )
            # asyncpg maps a PostgreSQL lock_timeout to SQLAlchemy DBAPIError,
            # not SQLAlchemy DatabaseError. Require the exact lock SQLSTATE.
            pg_error = locked.value.orig
            sqlstate = (
                getattr(pg_error, "sqlstate", None)
                or getattr(pg_error, "pgcode", None)
                or getattr(getattr(pg_error, "__cause__", None), "sqlstate", None)
            )
            assert sqlstate == "55P03"
            await updater.rollback()
            await creator.commit()
            await updater.execute(
                text("UPDATE users SET status='inactive' WHERE id='alice'")
            )
            await updater.commit()
        async with sessions() as s:
            with pytest.raises(AgentInstanceDenied):
                await AgentInstanceRepository(s).create("alice", "d", "v")
            await s.rollback()
        # Database-layer owner deletion must also fail when AIC live binding exists.
        async with sessions() as s:
            with pytest.raises(DBAPIError, match="AIC_LIVE_OWNER_DELETE_DENIED"):
                await s.execute(text("DELETE FROM users WHERE id='alice'"))
            await s.rollback()
        async with engine.begin() as conn:
            with pytest.raises(RuntimeError, match="downgrade denied"):
                await conn.run_sync(
                    lambda sync: _run_aic_migration(sync, direction="downgrade")
                )
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()


@pytest.mark.asyncio
async def test_aic_pg17_live_insert_serializes_against_nonkey_reservation_writer(postgres_url):
    """An ACTIVE marker + non-key reservation writer must block LIVE INSERT.

    A plain existence query plus FK key-share is insufficient: the physical
    no-key-update lock is compatible with key-share but not PG FOR SHARE.
    This is an actual two-connection PG17 negative concurrency regression.
    """
    schema = "aic426_race_" + uuid4().hex[:12]
    admin = create_async_engine(postgres_url)
    async with admin.begin() as c:
        version = int((await c.execute(text("SHOW server_version_num"))).scalar_one())
        if not 170000 <= version < 180000:
            pytest.skip("this gate requires PostgreSQL 17")
        await c.execute(text("CREATE SCHEMA " + schema))
    engine = create_async_engine(
        postgres_url, connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        await _tables(engine, dialect="postgresql")
        await _user(engine)
        async with engine.begin() as setup:
            await setup.execute(text(
                "INSERT INTO aic_instance_reservations "
                "(agent_instance_id,state,generation) "
                "VALUES ('fenced-race','ACTIVE',1)"
            ))
        async with engine.connect() as writer, engine.connect() as contender:
            # Hold the same row-lock class a normal non-key UPDATE uses.
            await writer.execute(text(
                "SELECT agent_instance_id FROM aic_instance_reservations "
                "WHERE agent_instance_id='fenced-race' FOR NO KEY UPDATE"
            ))
            await contender.execute(text("SET LOCAL lock_timeout='250ms'"))
            with pytest.raises(DBAPIError) as locked:
                await contender.execute(text(
                    "INSERT INTO aic_instance_live "
                    "(agent_instance_id,owner_user_id,definition_id,"
                    "definition_version,lifecycle_state,generation) "
                    "VALUES ('fenced-race','alice','def','v1','ACTIVE',1)"
                ))
            pg_error = locked.value.orig
            sqlstate = (
                getattr(pg_error, "sqlstate", None)
                or getattr(pg_error, "pgcode", None)
                or getattr(getattr(pg_error, "__cause__", None), "sqlstate", None)
            )
            assert sqlstate == "55P03", f"expected reservation-lock conflict, got {pg_error}"
            await contender.rollback()
            await writer.rollback()
        # No stale trigger authorization: a fresh legitimate bind is accepted
        # only after the concurrent writer releases its reservation lock.
        async with engine.begin() as fresh:
            await fresh.execute(text(
                "INSERT INTO aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,"
                "definition_version,lifecycle_state,generation) "
                "VALUES ('fenced-race','alice','def','v1','ACTIVE',1)"
            ))
            assert (await fresh.execute(text(
                "SELECT COUNT(*) FROM aic_instance_live "
                "WHERE agent_instance_id='fenced-race'"
            ))).scalar_one() == 1
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()


def _run_aic_migration(sync_connection, *, direction: str):
    previous = MIG.op
    try:
        MIG.op = Operations(MigrationContext.configure(sync_connection))
        if direction == "upgrade":
            MIG.upgrade()
        else:
            MIG.downgrade()
    finally:
        MIG.op = previous


@pytest.mark.parametrize("foreign_keys", [False, True], ids=["fk_off", "fk_on"])
@pytest.mark.parametrize(
    "recursive_triggers", [False, True], ids=["recursive_off", "recursive_on"]
)
@pytest.mark.parametrize(
    "replace_statement", ["INSERT OR REPLACE", "REPLACE"], ids=["insert_or_replace", "replace"]
)
@pytest.mark.asyncio
async def test_aic_actual_alembic_sqlite_retired_marker_cannot_be_replaced(
    tmp_path, foreign_keys, recursive_triggers, replace_statement
):
    """RED-first: actual Alembic 31a DDL rejects REPLACE after lawful retirement.

    Use separate native SQLite file connections after the Alembic upgrade; no
    model-only setup, faked triggers, PRAGMA recursive_triggers assumption, or
    weakening of any existing strict assertion.
    """
    import sqlite3

    file_path = tmp_path / (
        f"aic_retired_{int(foreign_keys)}_{int(recursive_triggers)}_"
        f"{replace_statement.replace(' ', '_')}.sqlite"
    )
    engine = create_async_engine("sqlite+aiosqlite:///" + str(file_path))
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            await connection.run_sync(User.__table__.create)
            await connection.run_sync(
                lambda sync: _run_aic_migration(sync, direction="upgrade")
            )
    finally:
        await engine.dispose()

    with sqlite3.connect(str(file_path)) as seed:
        seed.execute("PRAGMA foreign_keys=ON")
        seed.execute(
            "INSERT INTO users(id,email,password_hash,status) "
            "VALUES ('alice','alice@aic.invalid','x','active')"
        )
        seed.execute(
            "INSERT INTO users(id,email,password_hash,status) "
            "VALUES ('bob','bob@aic.invalid','x','active')"
        )
        seed.execute(
            "INSERT INTO aic_instance_reservations"
            "(agent_instance_id,state,generation) VALUES ('retired-id','ACTIVE',1)"
        )
        seed.execute(
            "INSERT INTO aic_instance_live"
            "(agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) "
            "VALUES ('retired-id','alice','original','v1','ACTIVE',1)"
        )
        seed.execute(
            "UPDATE aic_instance_reservations SET state='RETIRING' "
            "WHERE agent_instance_id='retired-id'"
        )
        seed.execute(
            "DELETE FROM aic_instance_live WHERE agent_instance_id='retired-id'"
        )
        seed.execute(
            "UPDATE aic_instance_reservations SET state='RETIRED',generation=2 "
            "WHERE agent_instance_id='retired-id'"
        )
        seed.commit()

    with sqlite3.connect(str(file_path)) as writer:
        writer.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
        writer.execute(
            f"PRAGMA recursive_triggers={'ON' if recursive_triggers else 'OFF'}"
        )
        assert writer.execute("PRAGMA foreign_keys").fetchone()[0] == int(foreign_keys)
        assert writer.execute("PRAGMA recursive_triggers").fetchone()[0] == int(
            recursive_triggers
        )
        guards = writer.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='trigger' AND name LIKE 'trg_aic_%'"
        ).fetchone()[0]
        assert guards == len(MIG._SQLITE_TRIGGER_NAMES) == 8

        with pytest.raises(sqlite3.IntegrityError, match="AIC_ID_REUSE_FORBIDDEN"):
            writer.execute(
                f"{replace_statement} INTO aic_instance_reservations "
                "(agent_instance_id,state,generation) VALUES "
                "('retired-id','ACTIVE',1)"
            )
        writer.rollback()

        # No previously-retired opaque key may be rebound to a new owner.
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_OR_ID_INVALID"):
            writer.execute(
                "INSERT INTO aic_instance_live"
                "(agent_instance_id,owner_user_id,definition_id,definition_version,"
                "lifecycle_state,generation) "
                "VALUES ('retired-id','bob','forged','v2','ACTIVE',1)"
            )
        writer.rollback()
        # Positive controls: normal fresh reservation and user no-op are valid.
        writer.execute(
            "INSERT INTO aic_instance_reservations"
            "(agent_instance_id,state,generation) VALUES ('fresh-id','ACTIVE',1)"
        )
        writer.execute(
            "INSERT INTO users(id,email,password_hash,status) "
            "VALUES ('alice','alice@aic.invalid','x','active') "
            "ON CONFLICT DO NOTHING"
        )
        writer.commit()

    # Fresh physical connection reads the committed identity and owner proof.
    with sqlite3.connect(str(file_path)) as verify:
        assert verify.execute(
            "SELECT state,generation FROM aic_instance_reservations "
            "WHERE agent_instance_id='retired-id'"
        ).fetchone() == ("RETIRED", 2)
        assert verify.execute(
            "SELECT COUNT(*) FROM aic_instance_live "
            "WHERE agent_instance_id='retired-id'"
        ).fetchone()[0] == 0
        assert verify.execute(
            "SELECT state,generation FROM aic_instance_reservations "
            "WHERE agent_instance_id='fresh-id'"
        ).fetchone() == ("ACTIVE", 1)
        assert verify.execute(
            "SELECT COUNT(*) FROM users WHERE id IN ('alice','bob')"
        ).fetchone()[0] == 2


@pytest.mark.parametrize("foreign_keys", [False, True], ids=["fk_off", "fk_on"])
@pytest.mark.parametrize(
    "recursive_triggers", [False, True], ids=["recursive_off", "recursive_on"]
)
@pytest.mark.parametrize(
    "replace_statement", ["INSERT OR REPLACE", "REPLACE"], ids=["insert_or_replace", "replace"]
)
@pytest.mark.asyncio
async def test_aic_actual_alembic_sqlite_live_owner_replace_denied(
    tmp_path, foreign_keys, recursive_triggers, replace_statement
):
    """RED-first: real Alembic 31a DDL must never REPLACE an existing LIVE owner.

    SQLite implicit REPLACE DELETE may skip BEFORE DELETE triggers with
    recursive_triggers OFF. All assertions apply to real file-backed SQLite
    using separate physical connections, including FK ON and FK OFF modes.
    """
    import sqlite3

    db = tmp_path / (
        f"aic_live_replace_{int(foreign_keys)}_{int(recursive_triggers)}_"
        f"{replace_statement.replace(' ', '_')}.sqlite"
    )
    engine = create_async_engine("sqlite+aiosqlite:///" + str(db))
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(
                lambda sync: _run_aic_migration(sync, direction="upgrade")
            )
    finally:
        await engine.dispose()

    with sqlite3.connect(str(db)) as seed:
        seed.execute("PRAGMA foreign_keys=ON")
        for uid in ("alice", "bob"):
            seed.execute(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES (?,?, 'x','active')",
                (uid, uid + "@aic.invalid"),
            )
        seed.execute(
            "INSERT INTO aic_instance_reservations"
            "(agent_instance_id,state,generation) VALUES ('bound-id','ACTIVE',1)"
        )
        seed.execute(
            "INSERT INTO aic_instance_live"
            "(agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) VALUES "
            "('bound-id','alice','original','v1','ACTIVE',1)"
        )
        seed.commit()

    with sqlite3.connect(str(db)) as writer:
        writer.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
        writer.execute(
            f"PRAGMA recursive_triggers={'ON' if recursive_triggers else 'OFF'}"
        )
        assert writer.execute("PRAGMA foreign_keys").fetchone()[0] == int(foreign_keys)
        assert writer.execute("PRAGMA recursive_triggers").fetchone()[0] == int(
            recursive_triggers
        )
        count = writer.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='trigger' AND name LIKE 'trg_aic_%'"
        ).fetchone()[0]
        assert count == len(MIG._SQLITE_TRIGGER_NAMES) == 8

        with pytest.raises(
            sqlite3.IntegrityError,
            match="AIC_LIVE_OWNER_REPLACE_DENIED|AIC_LIVE_DELETE_FORBIDDEN",
        ):
            writer.execute(
                f"{replace_statement} INTO aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,definition_version,"
                "lifecycle_state,generation) VALUES "
                "('bound-id','bob','forged','v2','ACTIVE',1)"
            )
        writer.rollback()

        # Positive lifecycle: creating another identity, suspending and
        # retiring a different LIVE row remain legitimate within one DB tx.
        writer.execute(
            "INSERT INTO aic_instance_reservations"
            "(agent_instance_id,state,generation) VALUES ('fresh-id','ACTIVE',1)"
        )
        writer.execute(
            "INSERT INTO aic_instance_live"
            "(agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) VALUES "
            "('fresh-id','alice','new','v1','ACTIVE',1)"
        )
        writer.execute(
            "UPDATE aic_instance_live SET lifecycle_state='SUSPENDED',generation=2 "
            "WHERE agent_instance_id='fresh-id'"
        )
        writer.execute(
            "UPDATE aic_instance_reservations SET state='RETIRING' "
            "WHERE agent_instance_id='fresh-id'"
        )
        writer.execute(
            "DELETE FROM aic_instance_live WHERE agent_instance_id='fresh-id'"
        )
        writer.execute(
            "UPDATE aic_instance_reservations SET state='RETIRED',generation=3 "
            "WHERE agent_instance_id='fresh-id'"
        )
        writer.commit()

    # Reopen a distinct connection to check durable ownership and rollback.
    with sqlite3.connect(str(db)) as verify:
        assert verify.execute(
            "SELECT owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation FROM aic_instance_live "
            "WHERE agent_instance_id='bound-id'"
        ).fetchone() == ("alice", "original", "v1", "ACTIVE", 1)
        assert verify.execute(
            "SELECT state,generation FROM aic_instance_reservations "
            "WHERE agent_instance_id='bound-id'"
        ).fetchone() == ("ACTIVE", 1)
        assert verify.execute(
            "SELECT state,generation FROM aic_instance_reservations "
            "WHERE agent_instance_id='fresh-id'"
        ).fetchone() == ("RETIRED", 3)
        assert verify.execute(
            "SELECT COUNT(*) FROM aic_instance_live "
            "WHERE agent_instance_id='fresh-id'"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_aic_actual_alembic_sqlite_empty_upgrade_downgrade(tmp_path):
    """Execute actual 31a upgrade/downgrade DDL, not hand-created model tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + str(tmp_path / "aic_migration.sqlite")
    )
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(
                lambda sync: _run_aic_migration(sync, direction="upgrade")
            )
            guards = (await conn.execute(text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='trigger' AND name LIKE 'trg_aic_%'"
            ))).scalar_one()
            assert guards == len(MIG._SQLITE_TRIGGER_NAMES)
            await conn.run_sync(
                lambda sync: _run_aic_migration(sync, direction="downgrade")
            )
            assert (await conn.execute(text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='aic_instance_live'"
            ))).scalar_one() == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_aic_actual_alembic_sqlite_downgrade_rejects_marker(tmp_path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + str(tmp_path / "aic_downgrade.sqlite")
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(
                lambda sync: _run_aic_migration(sync, direction="upgrade")
            )
            await conn.execute(text(
                "INSERT INTO aic_instance_reservations "
                "(agent_instance_id,state,generation) VALUES ('reserved','ACTIVE',1)"
            ))
            with pytest.raises(RuntimeError, match="downgrade denied"):
                await conn.run_sync(
                    lambda sync: _run_aic_migration(sync, direction="downgrade")
                )
            assert (await conn.execute(text(
                "SELECT COUNT(*) FROM aic_instance_reservations "
                "WHERE agent_instance_id='reserved'"
            ))).scalar_one() == 1
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# AIC-0-P1A durability, real UBQ 25a/26a DDL, and raw-FK-OFF negative gates.
# The SQLite tests below reopen the real file through independent native
# sqlite3 connections. They deliberately never mock the AIC SQL migration.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sqlite_url",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite://",
        "sqlite+aiosqlite:///file:aic_mem_test?mode=memory&cache=shared&uri=true",
    ],
)
@pytest.mark.asyncio
async def test_aic_sqlite_shared_memory_connections_fail_closed(sqlite_url):
    # AsyncSession identity is not evidence of distinct physical SQLite
    # connections for in-memory databases. Never mint owner authority there.
    engine = create_async_engine(sqlite_url)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            with pytest.raises(AgentInstanceDenied, match="in-memory"):
                await AgentInstanceRepository(session).create("alice", "d", "v")
            assert not session.in_transaction()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_aic_actual_sqlalchemy_uow_same_session_rollback(sqlite_aic):
    # Use the real production UoW adapter rather than the minimal _Uow facade
    # used in other tests; no added session/commit knobs.
    from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork

    engine, sessions = sqlite_aic

    class _Driver:
        def get_session(self):
            return sessions()

    async with SqlAlchemyUnitOfWork(_Driver()) as uow:
        assert uow.agent_instances.session is uow.session
        row = await uow.agent_instances.create("alice", "d", "v")
        provisional = row.agent_instance_id
        await uow.rollback()

    async with engine.connect() as connection:
        count = (await connection.execute(text(
            "SELECT COUNT(*) FROM aic_instance_reservations "
            "WHERE agent_instance_id=:id"
        ), {"id": provisional})).scalar_one()
        assert count == 0


def _install_exact_ubq_owner_delete_sqlite_triggers(db):
    """Install *unmodified* 25a/26a owner-DELETE SQL, not synthetic predicates.

    Only the owner-DELETE guards are installed, using minimal historical
    table columns referenced by those guards. This is a trigger coexistence
    check, NOT a claim that every Alembic predecessor has been migrated.
    """
    from unittest.mock import patch

    v25 = importlib.import_module(
        "se.src.infrastructure.storage.migrations.sql.versions."
        "25a_ubq1_user_budget_foundation"
    )
    v26 = importlib.import_module(
        "se.src.infrastructure.storage.migrations.sql.versions."
        "26a_ubq2_dual_accounting_bridge"
    )
    for table in (
        "user_budget_policies",
        "user_budget_accounts",
        "user_budget_windows",
        "user_tool_budget_usage",
        "user_budget_reservations",
        "user_budget_task_bindings",
    ):
        db.execute(f"CREATE TABLE {table} (owner_user_id TEXT NOT NULL)")

    seen = []

    def install_25(sql):
        if "CREATE TRIGGER trg_ubq_user_history_delete" in sql:
            db.execute(sql)
            seen.append("trg_ubq_user_history_delete")

    def install_26(sql):
        if "CREATE TRIGGER trg_ubq2_user_binding_delete_restrict" in sql:
            db.execute(sql)
            seen.append("trg_ubq2_user_binding_delete_restrict")

    with patch.object(v25, "_execute_sqlite_trigger", install_25):
        v25._create_sqlite_triggers()
    with patch.object(v26, "_sqlite", install_26):
        v26._create_sqlite_triggers()
    assert seen == [
        "trg_ubq_user_history_delete",
        "trg_ubq2_user_binding_delete_restrict",
    ]
    actual = {
        name for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        )
    }
    assert set(seen) | {"trg_aic_user_delete"} <= actual


@pytest.mark.parametrize("history", ["none", "25a", "26a", "both"])
@pytest.mark.asyncio
async def test_aic_exact_ubq_25a_26a_owner_delete_coexistence(sqlite_aic, history):
    # Independently opened native connections with FK enforcement actually OFF.
    import sqlite3
    from contextlib import closing

    engine, sessions = sqlite_aic
    identity_id = await _create(sessions)
    with closing(sqlite3.connect(engine.url.database)) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        _install_exact_ubq_owner_delete_sqlite_triggers(db)
        db.commit()
        # No UBQ history yet: the AIC guard alone must prevent an orphan.
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_DELETE_DENIED"):
            db.execute("DELETE FROM users WHERE id='alice'")
        db.rollback()
        assert db.execute(
            "SELECT COUNT(*) FROM aic_instance_live "
            "WHERE agent_instance_id=?", (identity_id,)
        ).fetchone()[0] == 1

    async with sessions() as session:
        assert await AgentInstanceRepository(session).retire(
            "alice", identity_id, 1
        ) == 2
        await session.commit()

    with closing(sqlite3.connect(engine.url.database)) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        if history in ("25a", "both"):
            db.execute(
                "INSERT INTO user_budget_policies(owner_user_id) VALUES ('alice')"
            )
        if history in ("26a", "both"):
            db.execute(
                "INSERT INTO user_budget_task_bindings(owner_user_id) VALUES ('alice')"
            )
        db.commit()
        if history == "none":
            assert db.execute("DELETE FROM users WHERE id='alice'").rowcount == 1
            db.commit()
        else:
            with pytest.raises(sqlite3.IntegrityError, match="UBQ"):
                db.execute("DELETE FROM users WHERE id='alice'")
            db.rollback()
            assert db.execute(
                "SELECT COUNT(*) FROM users WHERE id='alice'"
            ).fetchone()[0] == 1
        assert db.execute(
            "SELECT state FROM aic_instance_reservations "
            "WHERE agent_instance_id=?", (identity_id,)
        ).fetchone() == ("RETIRED",)
        assert db.execute(
            "SELECT COUNT(*) FROM aic_instance_live "
            "WHERE agent_instance_id=?", (identity_id,)
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_aic_sqlite_raw_fkoff_id_and_owner_mutation_fences(sqlite_aic):
    import sqlite3
    from contextlib import closing

    engine, sessions = sqlite_aic
    identity_id = await _create(sessions)
    with closing(sqlite3.connect(engine.url.database)) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        bad_sql = (
            ("UPDATE aic_instance_reservations SET generation=99 "
             "WHERE agent_instance_id=?", "AIC_ID_TRANSITION_FORBIDDEN"),
            ("DELETE FROM aic_instance_reservations "
             "WHERE agent_instance_id=?", "AIC_ID_REUSE_FORBIDDEN"),
            ("DELETE FROM aic_instance_live "
             "WHERE agent_instance_id=?", "AIC_LIVE_DELETE_FORBIDDEN"),
            ("UPDATE aic_instance_live SET definition_version='forged' "
             "WHERE agent_instance_id=?", "AIC_LIVE_UPDATE_FORBIDDEN"),
            ("UPDATE aic_instance_live SET owner_user_id='other' "
             "WHERE agent_instance_id=?", "AIC_LIVE_UPDATE_FORBIDDEN"),
            ("UPDATE aic_instance_live SET generation=4 "
             "WHERE agent_instance_id=?", "AIC_LIVE_UPDATE_FORBIDDEN"),
        )
        for statement, error in bad_sql:
            with pytest.raises(sqlite3.IntegrityError, match=error):
                db.execute(statement, (identity_id,))
            db.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_REKEY_DENIED"):
            db.execute("UPDATE users SET id='other' WHERE id='alice'")
        db.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="AIC_ID_INITIAL_STATE_INVALID"):
            db.execute(
                "INSERT INTO aic_instance_reservations "
                "(agent_instance_id,state,generation) VALUES ('forged','RETIRED',1)"
            )
        db.rollback()
        assert db.execute(
            "SELECT state,generation FROM aic_instance_reservations "
            "WHERE agent_instance_id=?", (identity_id,)
        ).fetchone() == ("ACTIVE", 1)


@pytest.mark.parametrize(
    ("cut", "expected_state", "expected_live"),
    [
        ("create_uncommitted", "ACTIVE", 1),
        ("retire_uncommitted", "ACTIVE", 1),
        ("retire_committed", "RETIRED", 0),
    ],
)
@pytest.mark.asyncio
async def test_aic_sqlite_real_process_hard_exit_restart_id_durability(
    sqlite_aic, cut, expected_state, expected_live
):
    # Spawn a completely separate Python process. os._exit(77) bypasses
    # finally/atexit and kills its SQLite connection. A new native connection
    # verifies real committed vs uncommitted physical file state after restart.
    import sqlite3
    from contextlib import closing

    engine, sessions = sqlite_aic
    identity_id = await _create(sessions)
    uncommitted_id = "inflight-" + uuid4().hex
    worker = r"""
import os, sqlite3, sys
path, cut, live_id, inflight_id = sys.argv[1:]
db = sqlite3.connect(path, timeout=5.0)
db.execute("PRAGMA foreign_keys=OFF")
assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
db.execute("BEGIN IMMEDIATE")
if cut == "create_uncommitted":
    db.execute(
        "INSERT INTO aic_instance_reservations "
        "(agent_instance_id,state,generation) VALUES (?, 'ACTIVE', 1)",
        (inflight_id,),
    )
    db.execute(
        "INSERT INTO aic_instance_live "
        "(agent_instance_id,owner_user_id,definition_id,definition_version,"
        "lifecycle_state,generation) VALUES (?, 'alice', 'd', 'v', 'ACTIVE', 1)",
        (inflight_id,),
    )
else:
    assert db.execute(
        "UPDATE aic_instance_reservations SET state='RETIRING' "
        "WHERE agent_instance_id=? AND state='ACTIVE'", (live_id,)
    ).rowcount == 1
    assert db.execute(
        "DELETE FROM aic_instance_live WHERE agent_instance_id=?", (live_id,)
    ).rowcount == 1
    assert db.execute(
        "UPDATE aic_instance_reservations "
        "SET state='RETIRED', generation=2 "
        "WHERE agent_instance_id=? AND state='RETIRING'", (live_id,)
    ).rowcount == 1
    if cut == "retire_committed":
        db.commit()
os._exit(77)
"""
    result = subprocess.run(
        [sys.executable, "-c", worker, engine.url.database, cut,
         identity_id, uncommitted_id],
        check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 77, (
        f"worker exit={result.returncode}, stderr={result.stderr[-1500:]}"
    )
    with closing(sqlite3.connect(engine.url.database)) as restarted:
        restarted.execute("PRAGMA foreign_keys=OFF")
        assert restarted.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert restarted.execute(
            "SELECT state FROM aic_instance_reservations "
            "WHERE agent_instance_id=?", (identity_id,)
        ).fetchone() == (expected_state,)
        assert restarted.execute(
            "SELECT COUNT(*) FROM aic_instance_live "
            "WHERE agent_instance_id=?", (identity_id,)
        ).fetchone()[0] == expected_live
        assert restarted.execute(
            "SELECT COUNT(*) FROM aic_instance_reservations "
            "WHERE agent_instance_id=?", (uncommitted_id,)
        ).fetchone()[0] == 0

        if cut == "retire_committed":
            # A fully committed ownerless terminal marker must survive crash,
            # forbid resurrection, direct deletion and any reuse of the ID.
            with pytest.raises(sqlite3.IntegrityError, match="AIC_ID_TRANSITION_FORBIDDEN"):
                restarted.execute(
                    "UPDATE aic_instance_reservations SET state='ACTIVE' "
                    "WHERE agent_instance_id=?", (identity_id,)
                )
            restarted.rollback()
            with pytest.raises(sqlite3.IntegrityError, match="AIC_ID_REUSE_FORBIDDEN"):
                restarted.execute(
                    "DELETE FROM aic_instance_reservations WHERE agent_instance_id=?",
                    (identity_id,),
                )
            restarted.rollback()
            with pytest.raises(sqlite3.IntegrityError):
                restarted.execute(
                    "INSERT INTO aic_instance_reservations "
                    "(agent_instance_id,state,generation) VALUES (?, 'ACTIVE', 1)",
                    (identity_id,),
                )
            restarted.rollback()
            with pytest.raises(sqlite3.IntegrityError, match="AIC_LIVE_OWNER_OR_ID_INVALID"):
                restarted.execute(
                    "INSERT INTO aic_instance_live "
                    "(agent_instance_id,owner_user_id,definition_id,"
                    "definition_version,lifecycle_state,generation) "
                    "VALUES (?, 'alice', 'forged', 'v1', 'ACTIVE', 1)",
                    (identity_id,),
                )
            restarted.rollback()

    if cut == "retire_uncommitted":
        # After a hard crash *before* COMMIT, the original LIVE binding and
        # reservation are still intact and can be retired exactly once.
        async with sessions() as session:
            assert await AgentInstanceRepository(session).retire(
                "alice", identity_id, 1
            ) == 2
            await session.commit()



@pytest.mark.asyncio
async def test_aic_raw_committed_intermediate_retiring_remains_fail_closed_after_restart(sqlite_aic):
    """Privileged raw partial commit must poison availability, never mint reuse.

    The normal repository never commits RETIRING. A privileged actor might;
    recovery must deny any ACTIVE binding rather than resurrect or auto-retire
    a marker that may still reference an owner. This is a documented operator
    repair boundary, not an account-erasure or cleanup authorization.
    """
    import sqlite3
    from contextlib import closing

    engine, sessions = sqlite_aic
    instance_id = await _create(sessions)
    with closing(sqlite3.connect(engine.url.database)) as actor:
        actor.execute("PRAGMA foreign_keys=OFF")
        actor.execute("BEGIN IMMEDIATE")
        assert actor.execute(
            "UPDATE aic_instance_reservations SET state='RETIRING' "
            "WHERE agent_instance_id=? AND state='ACTIVE'", (instance_id,),
        ).rowcount == 1
        actor.commit()
    # Reopen the database through a distinct physical native SQLite connection.
    with closing(sqlite3.connect(engine.url.database)) as restarted:
        restarted.execute("PRAGMA foreign_keys=OFF")
        assert restarted.execute(
            "SELECT state FROM aic_instance_reservations "
            "WHERE agent_instance_id=?", (instance_id,),
        ).fetchone() == ("RETIRING",)
        assert restarted.execute(
            "SELECT COUNT(*) FROM aic_instance_live WHERE agent_instance_id=?",
            (instance_id,),
        ).fetchone() == (1,)
        for statement, message in (
            ("DELETE FROM users WHERE id='alice'", "AIC_LIVE_OWNER_DELETE_DENIED"),
            ("UPDATE aic_instance_reservations SET state='ACTIVE' "
             "WHERE agent_instance_id=?", "AIC_ID_TRANSITION_FORBIDDEN"),
            ("UPDATE aic_instance_reservations SET state='RETIRED' "
             "WHERE agent_instance_id=?", "AIC_ID_TRANSITION_FORBIDDEN"),
            ("DELETE FROM aic_instance_reservations WHERE agent_instance_id=?",
             "AIC_ID_REUSE_FORBIDDEN"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=message):
                if "?" in statement:
                    restarted.execute(statement, (instance_id,))
                else:
                    restarted.execute(statement)
            restarted.rollback()
    async with sessions() as session:
        with pytest.raises(AgentInstanceDenied, match="unavailable"):
            await AgentInstanceRepository(session).resolve("alice", instance_id)
        await session.rollback()
    async with sessions() as session:
        with pytest.raises(AgentInstanceDenied, match="unavailable"):
            await AgentInstanceRepository(session).retire("alice", instance_id, 1)
        await session.rollback()


# ---------------------------------------------------------------------------
# PostgreSQL-17 ID durability: a REAL second OS interpreter dies by os._exit,
# not an in-process exception or mocked AsyncSession. Independent connections
# prove that committed retirement is permanent and interrupted writes roll back.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("cut", "expected_state", "expected_live"),
    [
        ("create_uncommitted", "ACTIVE", 1),
        ("retire_uncommitted", "ACTIVE", 1),
        ("retire_committed", "RETIRED", 0),
    ],
)
@pytest.mark.asyncio
async def test_aic_pg17_hard_exit_live_and_permanent_marker(
    postgres_url, cut, expected_state, expected_live
):
    import asyncpg

    if not os.getenv("AIC_TEST_POSTGRES_URL"):
        pytest.skip("Real PG17 OS-kill gate requires explicit disposable PG17 server")
    admin = create_async_engine(postgres_url)
    async with admin.begin() as c:
        version = int((await c.execute(text("SHOW server_version_num"))).scalar_one())
        assert 170000 <= version < 180000, "Native PostgreSQL 17 required"
    schema = "aic426_hardexit_" + uuid4().hex[:12]
    async with admin.begin() as c:
        await c.execute(text("CREATE SCHEMA " + schema))
    engine = create_async_engine(
        postgres_url, connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        await _tables(engine, dialect="postgresql")
        await _user(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        original_id = await _create(sessions)
        inflight_id = "inflight-" + uuid4().hex

        # The worker is a different operating-system process, connected with
        # asyncpg directly to the same REAL server. _exit defeats finally,
        # context-manager rollback and Python atexit cleanup.
        worker = r"""
import asyncio, asyncpg, os, sys
async def main():
    dsn, schema, cut, original_id, inflight_id = sys.argv[1:]
    connection = await asyncpg.connect(
        dsn=dsn, server_settings={"search_path": schema}
    )
    transaction = connection.transaction()
    await transaction.start()
    if cut == "create_uncommitted":
        await connection.execute(
            "INSERT INTO aic_instance_reservations "
            "(agent_instance_id,state,generation) VALUES ($1,'ACTIVE',1)",
            inflight_id,
        )
        await connection.execute(
            "INSERT INTO aic_instance_live "
            "(agent_instance_id,owner_user_id,definition_id,definition_version,"
            "lifecycle_state,generation) "
            "VALUES ($1,'alice','d','v','ACTIVE',1)", inflight_id,
        )
    else:
        assert await connection.execute(
            "UPDATE aic_instance_reservations SET state='RETIRING' "
            "WHERE agent_instance_id=$1 AND state='ACTIVE'", original_id,
        ) == "UPDATE 1"
        assert await connection.execute(
            "DELETE FROM aic_instance_live WHERE agent_instance_id=$1",
            original_id,
        ) == "DELETE 1"
        assert await connection.execute(
            "UPDATE aic_instance_reservations "
            "SET state='RETIRED',generation=2 "
            "WHERE agent_instance_id=$1 AND state='RETIRING'", original_id,
        ) == "UPDATE 1"
        if cut == "retire_committed":
            await transaction.commit()
    os._exit(77)
asyncio.run(main())
"""
        dsn = postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        outcome = subprocess.run(
            [sys.executable, "-c", worker, dsn, schema, cut, original_id, inflight_id],
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert outcome.returncode == 77, (
            f"PG subprocess exit={outcome.returncode} stderr={outcome.stderr[-1200:]}"
        )
        # Fresh physical connection after kernel process exit, no inherited
        # process-local repository/session or cached lifecycle status.
        restarted = create_async_engine(
            postgres_url, connect_args={"server_settings": {"search_path": schema}},
            pool_pre_ping=True,
        )
        try:
            async with restarted.connect() as c:
                assert (await c.execute(text(
                    "SELECT state FROM aic_instance_reservations "
                    "WHERE agent_instance_id=:id"
                ), {"id": original_id})).scalar_one() == expected_state
                assert (await c.execute(text(
                    "SELECT count(*) FROM aic_instance_live "
                    "WHERE agent_instance_id=:id"
                ), {"id": original_id})).scalar_one() == expected_live
                assert (await c.execute(text(
                    "SELECT count(*) FROM aic_instance_reservations "
                    "WHERE agent_instance_id=:id"
                ), {"id": inflight_id})).scalar_one() == 0
            if cut == "retire_committed":
                # A terminal marker is still present with no owner data and
                # can NEVER be removed, reset to ACTIVE, bound or re-inserted.
                for statement, needle in (
                    ("DELETE FROM aic_instance_reservations "
                     "WHERE agent_instance_id=:id", "AIC_ID_REUSE_FORBIDDEN"),
                    ("UPDATE aic_instance_reservations SET state='ACTIVE' "
                     "WHERE agent_instance_id=:id", "AIC_ID_TRANSITION_FORBIDDEN"),
                    ("INSERT INTO aic_instance_live "
                     "(agent_instance_id,owner_user_id,definition_id,"
                     "definition_version,lifecycle_state,generation) "
                     "VALUES (:id,'alice','d','v','ACTIVE',1)",
                     "AIC_LIVE_OWNER_OR_ID_INVALID"),
                ):
                    async with restarted.connect() as c:
                        with pytest.raises(DBAPIError, match=needle):
                            await c.execute(text(statement), {"id": original_id})
                        await c.rollback()
                async with restarted.connect() as c:
                    with pytest.raises(DBAPIError):
                        await c.execute(text(
                            "INSERT INTO aic_instance_reservations "
                            "(agent_instance_id,state,generation) "
                            "VALUES (:id,'ACTIVE',1)"
                        ), {"id": original_id})
                    await c.rollback()
                async with sessions() as s:
                    with pytest.raises(AgentInstanceDenied, match="unavailable"):
                        await AgentInstanceRepository(s).resolve("alice", original_id)
                    await s.rollback()
            elif cut == "retire_uncommitted":
                # Crash before COMMIT preserves legitimate LIVE owner, and the
                # normal caller UoW can retire it exactly once after restart.
                async with sessions() as s:
                    assert await AgentInstanceRepository(s).retire(
                        "alice", original_id, 1
                    ) == 2
                    await s.commit()
        finally:
            await restarted.dispose()
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()


@pytest.mark.asyncio
async def test_aic_pg17_install_triggers_on_durable_tables_despite_prior_temp_shadows(postgres_url):
    """Pre-install pg_temp shadows may NOT receive the AIC guard triggers.

    The attacker prepares TEMP relations in the migration session BEFORE 31a
    installs triggers. Assert physical pg_trigger.tgrelid/tgfoid match durable
    schema table/function OIDs, not search_path-resolved temporary objects.
    """
    if not os.getenv("AIC_TEST_POSTGRES_URL"):
        pytest.skip("requires actual disposable PostgreSQL17 server")
    admin = create_async_engine(postgres_url)
    schema = "aic426_install_shadow_" + uuid4().hex[:12]
    async with admin.begin() as c:
        assert 170000 <= int((await c.execute(text(
            "SHOW server_version_num"
        ))).scalar_one()) < 180000
        await c.execute(text("CREATE SCHEMA " + schema))
    engine = create_async_engine(
        postgres_url, connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        # Inject TEMP shadows in the REAL migration connection immediately
        # BEFORE its first CREATE TRIGGER, but AFTER permanent tables and FKs
        # have been built. Creating temp tables earlier can break unrelated
        # FK DDL and would not test the trigger installation vulnerability.
        from sqlalchemy import event

        async with engine.begin() as c:
            await c.run_sync(User.__table__.create)
            injected = {"done": False}

            def shadow_before_trigger(sync_conn, cursor, statement, parameters,
                                      context, executemany):
                if injected["done"] or not statement.lstrip().startswith(
                    "CREATE TRIGGER trg_aic_"
                ):
                    return
                injected["done"] = True
                for ddl in (
                    "CREATE TEMP TABLE users (id TEXT, status TEXT)",
                    "CREATE TEMP TABLE aic_instance_reservations "
                    "(agent_instance_id TEXT, state TEXT, generation BIGINT)",
                    "CREATE TEMP TABLE aic_instance_live "
                    "(agent_instance_id TEXT, owner_user_id TEXT)",
                ):
                    sync_conn.exec_driver_sql(ddl)
                # pg_temp now wins ordinary unqualified relation resolution;
                # the original unqualified CREATE TRIGGER was vulnerable.
                assert sync_conn.exec_driver_sql(
                    "SELECT to_regclass('users') = to_regclass('pg_temp.users')"
                ).scalar_one() is True

            event.listen(c.sync_connection, "before_cursor_execute",
                         shadow_before_trigger)
            try:
                await c.run_sync(
                    lambda sync: _run_aic_migration(sync, direction="upgrade")
                )
            finally:
                event.remove(c.sync_connection, "before_cursor_execute",
                             shadow_before_trigger)
            assert injected["done"], "real migration never reached CREATE TRIGGER"
            objects = (await c.execute(text(
                "SELECT t.tgname, n.nspname, rel.relname, fn.proname, fns.nspname "
                "FROM pg_catalog.pg_trigger AS t "
                "JOIN pg_catalog.pg_class AS rel ON rel.oid=t.tgrelid "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid=rel.relnamespace "
                "JOIN pg_catalog.pg_proc AS fn ON fn.oid=t.tgfoid "
                "JOIN pg_catalog.pg_namespace AS fns ON fns.oid=fn.pronamespace "
                "WHERE t.tgname IN "
                "('trg_aic_id_guard','trg_aic_live_guard',"
                "'trg_aic_user_delete','trg_aic_user_rekey')"
            ))).all()
            assert len(objects) == 4, objects
            assert {obj[0] for obj in objects} == {
                "trg_aic_id_guard", "trg_aic_live_guard",
                "trg_aic_user_delete", "trg_aic_user_rekey",
            }
            expected_tables = {
                "trg_aic_id_guard": ("aic_instance_reservations", "aic_id_guard"),
                "trg_aic_live_guard": ("aic_instance_live", "aic_live_guard"),
                "trg_aic_user_delete": ("users", "aic_user_guard"),
                "trg_aic_user_rekey": ("users", "aic_user_guard"),
            }
            for trig, table_schema, table_name, fn_name, function_schema in objects:
                assert (table_name, fn_name) == expected_tables[trig]
                assert table_schema == schema, (trig, table_schema)
                assert function_schema == schema, (trig, function_schema)
        # Drop the pool so the post-install checks cannot reuse the migration
        # connection's TEMP shadow relations under asyncpg connection pooling.
        await engine.dispose()
        # A fresh physical session must see genuine tables and reject reuse.
        await _user(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        original_id = await _create(sessions)
        async with sessions() as s:
            assert await AgentInstanceRepository(s).retire(
                "alice", original_id, 1
            ) == 2
            await s.commit()
        async with engine.connect() as c:
            await c.execute(text(
                "CREATE TEMP TABLE aic_instance_reservations "
                "(agent_instance_id TEXT, state TEXT)"
            ))
            await c.execute(text("CREATE TEMP TABLE users (id TEXT, status TEXT)"))
            await c.execute(text(
                "INSERT INTO pg_temp.aic_instance_reservations VALUES (:id,'ACTIVE')"
            ), {"id": original_id})
            await c.execute(text(
                "INSERT INTO pg_temp.users VALUES ('alice','active')"
            ))
            await c.execute(text("SET LOCAL search_path TO pg_temp, " + schema))
            with pytest.raises(DBAPIError, match="AIC_LIVE_OWNER_OR_ID_INVALID"):
                await c.execute(text(
                    "INSERT INTO " + schema + ".aic_instance_live "
                    "(agent_instance_id,owner_user_id,definition_id,"
                    "definition_version,lifecycle_state,generation) "
                    "VALUES (:id,'alice','forged','v1','ACTIVE',1)"
                ), {"id": original_id})
            await c.rollback()
        async with engine.connect() as c:
            assert (await c.execute(text(
                "SELECT state FROM aic_instance_reservations "
                "WHERE agent_instance_id=:id"
            ), {"id": original_id})).scalar_one() == "RETIRED"
            assert (await c.execute(text(
                "SELECT COUNT(*) FROM aic_instance_live WHERE agent_instance_id=:id"
            ), {"id": original_id})).scalar_one() == 0
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()


@pytest.mark.asyncio
async def test_aic_pg17_trigger_rejects_search_path_shadow_resurrection(postgres_url):
    """Retired permanent ID must not be rebound via session search_path.

    An untrusted DB session can create pg_temp relations under ordinary TEMP
    privilege. PL/pgSQL trigger SELECTs must read the SAME schema as the
    trigger's AIC tables rather than caller-shadowed relations. This is an
    intentional NEGATIVE gate against marker resurrection.
    """
    if not os.getenv("AIC_TEST_POSTGRES_URL"):
        pytest.skip("requires disposable real PG17 server")
    admin = create_async_engine(postgres_url)
    async with admin.begin() as c:
        assert 170000 <= int((await c.execute(text(
            "SHOW server_version_num"
        ))).scalar_one()) < 180000
    schema = "aic426_shadow_" + uuid4().hex[:12]
    async with admin.begin() as c:
        await c.execute(text("CREATE SCHEMA " + schema))
    engine = create_async_engine(
        postgres_url, connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        await _tables(engine, dialect="postgresql")
        # Check all three database guard functions, not just LIVE.
        # Runtime search_path and implicit pg_temp must not redirect any
        # owner/marker query away from the newly created AIC schema.
        async with engine.connect() as c:
            pins = (await c.execute(text(
                "SELECT p.proname, array_to_string(p.proconfig, ',') "
                "FROM pg_catalog.pg_proc AS p "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid=p.pronamespace "
                "WHERE n.nspname=:schema "
                "AND p.proname IN ('aic_id_guard','aic_live_guard','aic_user_guard')"
            ), {"schema": schema})).all()
        assert len(pins) == 3
        for _function, config in pins:
            assert config and config.startswith("search_path="), _function
            assert config.split("=", 1)[1].replace('"', "").replace(" ", "").split(",") == [
                "pg_catalog", schema, "pg_temp"
            ], (_function, config)
        await _user(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        old_id = await _create(sessions)
        async with sessions() as s:
            assert await AgentInstanceRepository(s).retire("alice", old_id, 1) == 2
            await s.commit()
        async with engine.connect() as c:
            assert (await c.execute(text(
                "SELECT state FROM aic_instance_reservations "
                "WHERE agent_instance_id=:id"
            ), {"id": old_id})).scalar_one() == "RETIRED"
            assert (await c.execute(text(
                "SELECT count(*) FROM aic_instance_live WHERE agent_instance_id=:id"
            ), {"id": old_id})).scalar_one() == 0
        async with engine.connect() as c:
            # Same physical connection, malicious caller creates temp tables
            # and changes path, but explicitly writes the real AIC live table.
            await c.execute(text(
                "CREATE TEMP TABLE aic_instance_reservations "
                "(agent_instance_id TEXT, state TEXT)"
            ))
            await c.execute(text(
                "CREATE TEMP TABLE users (id TEXT, status TEXT)"
            ))
            await c.execute(text(
                "INSERT INTO pg_temp.aic_instance_reservations "
                "(agent_instance_id,state) VALUES (:id,'ACTIVE')"
            ), {"id": old_id})
            await c.execute(text(
                "INSERT INTO pg_temp.users(id,status) VALUES ('alice','active')"
            ))
            await c.execute(text("SET LOCAL search_path TO pg_temp, " + schema))
            with pytest.raises(DBAPIError, match="AIC_LIVE_OWNER_OR_ID_INVALID"):
                await c.execute(text(
                    "INSERT INTO " + schema + ".aic_instance_live "
                    "(agent_instance_id,owner_user_id,definition_id,"
                    "definition_version,lifecycle_state,generation) "
                    "VALUES (:id,'alice','forged','v','ACTIVE',1)"
                ), {"id": old_id})
            await c.rollback()
        async with engine.connect() as c:
            assert (await c.execute(text(
                "SELECT count(*) FROM aic_instance_live WHERE agent_instance_id=:id"
            ), {"id": old_id})).scalar_one() == 0

        # Separate adversarial probe for aic_id_guard: pg_temp LIVE rows
        # cannot forge the real LIVE prerequisite for ACTIVE -> RETIRING.
        # A deliberately bare reservation mimics an interrupted/illicit raw
        # SQL write; it must remain ACTIVE instead of trusting a TEMP shadow.
        bare_id = "bare-" + uuid4().hex
        async with engine.begin() as c:
            await c.execute(text(
                "INSERT INTO aic_instance_reservations "
                "(agent_instance_id,state,generation) VALUES (:id,'ACTIVE',1)"
            ), {"id": bare_id})
        async with engine.connect() as c:
            await c.execute(text(
                "CREATE TEMP TABLE aic_instance_live "
                "(agent_instance_id TEXT, owner_user_id TEXT)"
            ))
            await c.execute(text(
                "INSERT INTO pg_temp.aic_instance_live "
                "(agent_instance_id,owner_user_id) VALUES (:id,'alice')"
            ), {"id": bare_id})
            await c.execute(text("SET LOCAL search_path TO pg_temp, " + schema))
            with pytest.raises(DBAPIError, match="AIC_ID_TRANSITION_FORBIDDEN"):
                await c.execute(text(
                    "UPDATE " + schema + ".aic_instance_reservations "
                    "SET state='RETIRING' WHERE agent_instance_id=:id"
                ), {"id": bare_id})
            await c.rollback()
        async with engine.connect() as c:
            assert (await c.execute(text(
                "SELECT state FROM aic_instance_reservations "
                "WHERE agent_instance_id=:id"
            ), {"id": bare_id})).scalar_one() == "ACTIVE"

        # Third guard: an empty TEMP LIVE table cannot trick aic_user_guard
        # into permitting rekey of a genuine owner who still has LIVE data.
        live_id = await _create(sessions)
        async with engine.connect() as c:
            await c.execute(text(
                "CREATE TEMP TABLE aic_instance_live "
                "(agent_instance_id TEXT, owner_user_id TEXT)"
            ))
            await c.execute(text("SET LOCAL search_path TO pg_temp, " + schema))
            with pytest.raises(DBAPIError, match="AIC_LIVE_OWNER_REKEY_DENIED"):
                await c.execute(text(
                    "UPDATE " + schema + ".users SET id='forged-user' "
                    "WHERE id='alice'"
                ))
            await c.rollback()
        async with engine.connect() as c:
            assert (await c.execute(text(
                "SELECT owner_user_id FROM aic_instance_live "
                "WHERE agent_instance_id=:id"
            ), {"id": live_id})).scalar_one() == "alice"
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()


# ---------------------------------------------------------------------------
# Full physical Alembic history gate: base→24a→25a→26a→27a→28a→29a→30a→31a.
# Unlike the scoped trigger tests above, this invokes the real CLI/env.py and
# upgrades an actual populated database in separate operating-system processes.
# ---------------------------------------------------------------------------


def _aic_run_real_alembic(url, revision):
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    env = dict(os.environ, ASSISTANT_ALEMBIC_DATABASE_URL=url)
    invocation = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", revision],
        cwd=str(root), env=env, capture_output=True, text=True, timeout=180,
        check=False,
    )
    assert invocation.returncode == 0, (
        f"Real Alembic upgrade to {revision} failed (exit={invocation.returncode}).\n"
        f"STDOUT:\n{invocation.stdout[-3000:]}\n"
        f"STDERR:\n{invocation.stderr[-6000:]}"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.asyncio
async def test_aic_full_historical_alembic_chain_with_ubq25_26_and_live_owner(
    tmp_path, request, backend
):
    """Actual old-schema rows survive ALL intermediate production migrations.

    PostgreSQL runs against a fresh disposable real database; SQLite against a
    file with FK-OFF. Both run alembic via fresh child interpreter/processes.
    """
    import sqlite3
    from sqlalchemy.engine import make_url

    db_name = "aic_fullchain_" + uuid4().hex[:16]
    admin = None
    if backend == "postgresql":
        # Full-suite Linux may have an unrelated default-version PostgreSQL.
        # Only explicit external PG17 is valid for this migration evidence.
        if not os.getenv("AIC_TEST_POSTGRES_URL"):
            pytest.skip("PG17 historical migration needs explicit AIC_TEST_POSTGRES_URL")
        parent_url = request.getfixturevalue("postgres_url")
        admin = create_async_engine(parent_url, isolation_level="AUTOCOMMIT")
        async with admin.connect() as c:
            version = int((await c.execute(text("SHOW server_version_num"))).scalar_one())
            assert 170000 <= version < 180000, (
                f"Full migration requires real PostgreSQL 17, got {version}"
            )
            await c.execute(text("CREATE DATABASE " + db_name))
        url = make_url(parent_url).set(database=db_name).render_as_string(
            hide_password=False
        )
    else:
        url = "sqlite+aiosqlite:///" + str(tmp_path / (db_name + ".sqlite"))

    engine = create_async_engine(url, pool_pre_ping=True)
    old_policy = "legacy-policy-" + db_name
    old_task = "legacy-task-" + db_name
    old_receipt = "legacy-receipt-" + db_name
    marker = "permanent-aic-" + db_name

    async def read_scalar(sql, params=None):
        async with engine.connect() as cx:
            return (await cx.execute(text(sql), params or {})).scalar_one()

    async def require_revision(revision):
        # Alembic CLI runs in a separate process; PostgreSQL asyncpg caches
        # prepared statements across physical connections. After external DDL,
        # discard only the test pool (not schema/history) before reading.
        await engine.dispose()
        assert await read_scalar("SELECT version_num FROM alembic_version") == revision

    try:
        # Start a pristine database at its default Alembic version table.
        # This intentionally performs no test-side stamp, ALTER or revision
        # bypass: historical migration 5b itself must support PG17 before
        # the full populated 24a→25a→26a→30a→31a upgrade can be certified.
        # Untouched historical 5b is a known negative control (VARCHAR(32)).
        # Execute all ancestors with genuine out-of-process Alembic CLI.
        _aic_run_real_alembic(url, "24a_r12_stale_lease_scan_index")
        await require_revision("24a_r12_stale_lease_scan_index")
        async with engine.begin() as cx:
            if backend == "sqlite":
                await cx.exec_driver_sql("PRAGMA foreign_keys=OFF")
                assert (await cx.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 0
            await cx.execute(text(
                "INSERT INTO users(id,email,password_hash,status) "
                "VALUES ('alice','alice@aic-full.invalid','x','active')"
            ))
            await cx.execute(text(
                "INSERT INTO agent_tasks "
                "(id,session_id,created_by,assigned_agent_id,status,wait_reasons,input) "
                "VALUES (:task,'legacy-session','alice','legacy-agent',"
                "'CREATED','[]','{}')"
            ), {"task": old_task})
            await cx.execute(text(
                "INSERT INTO agent_task_budgets "
                "(task_id,max_total_executions,max_active_executions,"
                "max_active_branches,max_parallel_agents,max_total_tool_calls,"
                "max_total_inference_calls,max_delegation_depth,"
                "policy_version,policy_fingerprint) "
                "VALUES (:task,10,3,3,3,10,10,3,'legacy-v1',:fp)"
            ), {"task": old_task, "fp": "b" * 64})
            await cx.execute(text(
                "INSERT INTO agent_task_budget_reservations "
                "(task_id,kind,reservation_key,payload_fingerprint) "
                "VALUES (:task,'USAGE','legacy-usage',:fp)"
            ), {"task": old_task, "fp": "r" * 64})

        _aic_run_real_alembic(url, "25a_ubq1_user_budget_foundation")
        await require_revision("25a_ubq1_user_budget_foundation")
        async with engine.begin() as cx:
            await cx.execute(text(
                "INSERT INTO user_budget_policies "
                "(policy_id,owner_user_id,policy_version,policy_fingerprint,"
                "window_duration_seconds,tool_limits_json) "
                "VALUES (:policy,'alice','legacy-v1',:fp,3600,'{}')"
            ), {"policy": old_policy, "fp": "a" * 64})
            await cx.execute(text(
                "INSERT INTO user_budget_accounts(owner_user_id,next_window_epoch) "
                "VALUES ('alice',1)"
            ))
        assert await read_scalar(
            "SELECT COUNT(*) FROM user_budget_policies WHERE owner_user_id='alice'"
        ) == 1

        _aic_run_real_alembic(url, "26a_ubq2_dual_accounting_bridge")
        await require_revision("26a_ubq2_dual_accounting_bridge")
        # 26a MUST backfill historical pre-26a TaskBudget and reservation IDs
        # to one same durable generation and allocate the next generation.
        assert await read_scalar(
            "SELECT incarnation_generation FROM agent_task_budgets "
            "WHERE task_id=:task", {"task": old_task}
        ) == 1
        assert await read_scalar(
            "SELECT task_budget_incarnation_generation "
            "FROM agent_task_budget_reservations WHERE task_id=:task",
            {"task": old_task}
        ) == 1
        assert await read_scalar(
            "SELECT next_generation FROM agent_task_budget_incarnation_allocator "
            "WHERE allocator_id=1"
        ) == 2

        async with engine.begin() as cx:
            await cx.execute(text(
                "INSERT INTO user_budget_task_bindings "
                "(task_id,owner_user_id,enrollment_version,source_auth_type,"
                "resolution_fingerprint) "
                "VALUES (:task,'alice','ubq2-v1','jwt',:fp)"
            ), {"task": old_task, "fp": "c" * 64})
            await cx.execute(text(
                "INSERT INTO user_budget_dual_accounting_receipts "
                "(bridge_receipt_id,owner_user_id,task_id,task_budget_kind,"
                "task_budget_reservation_key,mirror_dimension,"
                "source_payload_fingerprint,amount_atomic) "
                "VALUES (:receipt,'alice',:task,'USAGE','legacy-usage',"
                "'NO_CHARGE',:fp,0)"
            ), {"receipt": old_receipt, "task": old_task, "fp": "r" * 64})

        # This upgrade traverses ALL intermediate 27a/28a/29a revisions.
        _aic_run_real_alembic(url, "30a_ctx_f5_user_wide_memory_scope")
        await require_revision("30a_ctx_f5_user_wide_memory_scope")
        assert await read_scalar(
            "SELECT COUNT(*) FROM user_budget_dual_accounting_receipts "
            "WHERE bridge_receipt_id=:receipt", {"receipt": old_receipt}
        ) == 1

        _aic_run_real_alembic(url, "31a_aic0_agent_instance_identity")
        await require_revision("31a_aic0_agent_instance_identity")
        assert await read_scalar(
            "SELECT COUNT(*) FROM user_budget_policies WHERE policy_id=:policy",
            {"policy": old_policy}
        ) == 1
        assert await read_scalar(
            "SELECT COUNT(*) FROM user_budget_task_bindings WHERE task_id=:task",
            {"task": old_task}
        ) == 1
        assert await read_scalar(
            "SELECT next_generation FROM agent_task_budget_incarnation_allocator "
            "WHERE allocator_id=1"
        ) == 2

        # Require raw owner deletion to fail with actual immutable historical
        # 25a/26a facts and real 31a owner-bound LIVE row (even with FK-OFF).
        async with engine.begin() as cx:
            if backend == "sqlite":
                await cx.exec_driver_sql("PRAGMA foreign_keys=OFF")
            await cx.execute(text(
                "INSERT INTO aic_instance_reservations "
                "(agent_instance_id,state,generation) VALUES (:id,'ACTIVE',1)"
            ), {"id": marker})
            await cx.execute(text(
                "INSERT INTO aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,"
                "definition_version,lifecycle_state,generation) "
                "VALUES (:id,'alice','legacy-definition','v1','ACTIVE',1)"
            ), {"id": marker})

        async with engine.connect() as cx:
            if backend == "sqlite":
                await cx.exec_driver_sql("PRAGMA foreign_keys=OFF")
                assert (await cx.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 0
            with pytest.raises(DBAPIError):
                await cx.execute(text("DELETE FROM users WHERE id='alice'"))
            await cx.rollback()

        assert await read_scalar(
            "SELECT COUNT(*) FROM aic_instance_live "
            "WHERE agent_instance_id=:id", {"id": marker}
        ) == 1
        assert await read_scalar(
            "SELECT COUNT(*) FROM user_budget_dual_accounting_receipts "
            "WHERE bridge_receipt_id=:id", {"id": old_receipt}
        ) == 1
        # No silent stamp, dropped old budget, reordered generations or
        # accidental replacement of old policy data is acceptable.
        await require_revision("31a_aic0_agent_instance_identity")
        # Evidence-only #494+#496+#500 composed descendant: advance the real
        # populated 25a/26a historical DB through actual 31b as well.
        _aic_run_real_alembic(url, "31b_aic0_pg_guard_forward")
        await require_revision("31b_aic0_pg_guard_forward")
        assert await read_scalar(
            "SELECT COUNT(*) FROM user_budget_dual_accounting_receipts "
            "WHERE bridge_receipt_id=:id", {"id": old_receipt}
        ) == 1
        assert await read_scalar(
            "SELECT COUNT(*) FROM user_budget_policies WHERE policy_id=:id",
            {"id": old_policy}
        ) == 1
        assert await read_scalar(
            "SELECT COUNT(*) FROM aic_instance_live WHERE agent_instance_id=:id",
            {"id": marker}
        ) == 1
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.connect() as c:
                await c.execute(text("DROP DATABASE IF EXISTS " + db_name + " WITH (FORCE)"))
            await admin.dispose()


# ---------------------------------------------------------------------------
# 31b forward-security witness on an actually installed old 31a function set.
# This invokes the pinned old Alembic migration DDL, NOT a fabricated function
# body, fake DB backend, fake trigger, or a replay of edited 31a source.
# A second, composed PR494 lane must separately prove real Alembic CLI
# revision-stamped 31a -> 31b on full 24a/25a/26a/30a ancestry.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aic_pg17_forward_old31a_guard_and_restart(postgres_url):
    import hashlib
    import types

    if not os.getenv("AIC_TEST_POSTGRES_URL"):
        pytest.skip("AIC 31b requires native PostgreSQL 17")
    forward = importlib.import_module(
        "se.src.infrastructure.storage.migrations.sql.versions."
        "31b_aic0_pg_guard_forward"
    )
    root = Path(__file__).resolve().parents[3]
    old_ref = "6edd86a050cb925939c40468e710d286abaaff41"
    old_path = (
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "31a_aic0_agent_instance_identity.py"
    )
    pinned = subprocess.run(
        ["git", "show", old_ref + ":" + old_path],
        cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, timeout=25,
    ).stdout
    raw_blob = b"blob " + str(len(pinned)).encode() + b"\0" + pinned
    assert hashlib.sha1(raw_blob).hexdigest() == (
        "3cb7528e622a26a62f2d4010986bfdd47df17873"
    ), "historical 31a exact source blob changed"
    historical = types.ModuleType("_aic_actual_old_31a")
    exec(compile(pinned, old_path, "exec"), historical.__dict__)
    assert historical.revision == "31a_aic0_agent_instance_identity"
    assert historical.down_revision == "30a_ctx_f5_user_wide_memory_scope"

    def run_migration(sync_connection, module):
        saved = module.op
        try:
            module.op = Operations(MigrationContext.configure(sync_connection))
            module.upgrade()
        finally:
            module.op = saved

    admin = create_async_engine(postgres_url)
    schema = "aic31b_oldinstalled_" + uuid4().hex[:12]
    async with admin.begin() as conn:
        assert 170000 <= int((await conn.execute(text(
            "SHOW server_version_num"
        ))).scalar_one()) < 180000
        await conn.execute(text("CREATE SCHEMA " + schema))
    engine = create_async_engine(
        postgres_url, connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(lambda sync: run_migration(sync, historical))
            old_config = (await conn.execute(text(
                "SELECT p.proconfig FROM pg_catalog.pg_proc p "
                "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname=:schema AND p.proname='aic_live_guard'"
            ), {"schema": schema})).scalar_one()
            assert not old_config, "old 31a must be genuinely unpinned"

        await _user(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        retired_id = await _create(sessions)
        async with sessions() as s:
            assert await AgentInstanceRepository(s).suspend(
                "alice", retired_id, 1
            ) == 2
            await s.commit()
        async with sessions() as s:
            assert await AgentInstanceRepository(s).retire(
                "alice", retired_id, 2
            ) == 3
            await s.commit()

        # Demonstrate the real historical weakness BEFORE the forward repair;
        # roll back the attack so 31b starts from a coherent durable state.
        async with engine.connect() as conn:
            await conn.execute(text(
                "CREATE TEMP TABLE users (id TEXT, status TEXT)"
            ))
            await conn.execute(text(
                "CREATE TEMP TABLE aic_instance_reservations "
                "(agent_instance_id TEXT, state TEXT)"
            ))
            await conn.execute(text(
                "INSERT INTO pg_temp.users VALUES ('alice','active')"
            ))
            await conn.execute(text(
                "INSERT INTO pg_temp.aic_instance_reservations "
                "VALUES (:i, 'ACTIVE')"
            ), {"i": retired_id})
            await conn.execute(text("SET LOCAL search_path TO pg_temp, " + schema))
            await conn.execute(text(
                "INSERT INTO " + schema + ".aic_instance_live "
                "(agent_instance_id,owner_user_id,definition_id,"
                "definition_version,lifecycle_state,generation) "
                "VALUES (:i,'alice','forged','v1','ACTIVE',1)"
            ), {"i": retired_id})
            assert (await conn.execute(text(
                "SELECT COUNT(*) FROM " + schema + ".aic_instance_live "
                "WHERE agent_instance_id=:i"
            ), {"i": retired_id})).scalar_one() == 1, (
                "old-31a exploit precondition did not reproduce"
            )
            await conn.rollback()

        # Three physical DDL tamper negatives on a genuine old-31a database:
        # wrong UPDATE OF column, a false WHEN, and extra trigger arguments.
        # Every attempt must be refused before any 31b guard/body is committed.
        malformed = {
            "wrong_id_column": (
                f"BEFORE UPDATE OF status ON {schema}.users "
                f"FOR EACH ROW EXECUTE FUNCTION {schema}.aic_user_guard()"
            ),
            "conditional_false": (
                f"BEFORE UPDATE OF id ON {schema}.users "
                f"FOR EACH ROW WHEN (FALSE) "
                f"EXECUTE FUNCTION {schema}.aic_user_guard()"
            ),
            "unexpected_arguments": (
                f"BEFORE UPDATE OF id ON {schema}.users "
                f"FOR EACH ROW EXECUTE FUNCTION "
                f"{schema}.aic_user_guard('unexpected')"
            ),
        }
        for case, definition in malformed.items():
            async with engine.connect() as conn:
                await conn.execute(text(
                    f"DROP TRIGGER trg_aic_user_rekey ON {schema}.users"
                ))
                await conn.execute(text(
                    "CREATE TRIGGER trg_aic_user_rekey " + definition
                ))
                with pytest.raises(RuntimeError, match="invalid physical guard trigger"):
                    await conn.run_sync(
                        lambda sync: run_migration(sync, forward)
                    )
                # Revert the attempted attacker-owned DDL and any 31b work.
                await conn.rollback()
            async with engine.connect() as conn:
                assert (await conn.execute(text(
                    "SELECT p.proconfig FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=:schema AND p.proname='aic_live_guard'"
                ), {"schema": schema})).scalar_one() is None, case

        # A blocked user/reservation/live writer can acquire table locks
        # in different orders from old 31a triggers. Migration must NEVER
        # wait while holding prior ACCESS EXCLUSIVE locks (deadlock risk).
        # NOWAIT refuses all three real contention shapes, then succeeds
        # only after the writer ends. This is a retry-after-quiescence gate.
        competing_live_id = await _create(sessions)
        contested_writers = (
            ("users",
             f"UPDATE {schema}.users SET status=status WHERE id='alice'", {}),
            ("reservation",
             f"UPDATE {schema}.aic_instance_reservations "
             "SET state='RETIRING' WHERE agent_instance_id=:i",
             {"i": competing_live_id}),
            ("live",
             f"UPDATE {schema}.aic_instance_live "
             "SET lifecycle_state='SUSPENDED', generation=2 "
             "WHERE agent_instance_id=:i",
             {"i": competing_live_id}),
        )
        for contested_table, sql, params in contested_writers:
            async with engine.connect() as writer:
                result = await writer.execute(text(sql), params)
                assert result.rowcount == 1, contested_table
                async with engine.connect() as migration:
                    started = asyncio.get_running_loop().time()
                    with pytest.raises(DBAPIError) as exc_info:
                        await migration.run_sync(
                            lambda sync: run_migration(sync, forward)
                        )
                    elapsed = asyncio.get_running_loop().time() - started
                    assert elapsed < 5, (contested_table, elapsed)
                    assert "lock" in str(exc_info.value).lower()
                    await migration.rollback()
                await writer.rollback()
            async with engine.connect() as check:
                assert (await check.execute(text(
                    "SELECT p.proconfig FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=:schema AND p.proname='aic_live_guard'"
                ), {"schema": schema})).scalar_one() is None, contested_table

        async with engine.begin() as migration:
            # No competing DML remains: one clean transactional repair.
            await migration.run_sync(
                lambda sync: run_migration(sync, forward)
            )

        async with engine.begin() as conn:
            # 31b was physically committed only AFTER writer lock release.
            for fn in ("aic_id_guard", "aic_live_guard", "aic_user_guard"):
                config = (await conn.execute(text(
                    "SELECT p.proconfig FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=:schema AND p.proname=:fn"
                ), {"schema": schema, "fn": fn})).scalar_one()
                assert config and len(config) == 1, (fn, config)
                assert config[0].startswith("search_path=pg_catalog,")
                assert config[0].endswith(", pg_temp")
            attached = (await conn.execute(text(
                "SELECT t.tgname, rn.nspname, rel.relname, pn.nspname, p.proname "
                "FROM pg_catalog.pg_trigger t "
                "JOIN pg_catalog.pg_class rel ON rel.oid=t.tgrelid "
                "JOIN pg_catalog.pg_namespace rn ON rn.oid=rel.relnamespace "
                "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
                "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
                "WHERE rn.nspname=:schema AND t.tgname LIKE 'trg_aic_%'"
            ), {"schema": schema})).all()
            assert len(attached) == 4, attached
            assert all(table_ns == schema and fn_ns == schema
                       for _, table_ns, _, fn_ns, _ in attached)

        # A fresh OS process creates fresh caller-controlled TEMP shadows,
        # tries real durable LIVE rebinding, then hard-exits without cleanup.
        worker = r"""
import asyncio, asyncpg, os, sys
async def main():
    dsn, schema, retired_id = sys.argv[1:]
    c = await asyncpg.connect(dsn=dsn, server_settings={"search_path": schema})
    await c.execute("CREATE TEMP TABLE users (id TEXT, status TEXT)")
    await c.execute("CREATE TEMP TABLE aic_instance_reservations (agent_instance_id TEXT, state TEXT)")
    await c.execute("INSERT INTO pg_temp.users VALUES ('alice','active')")
    await c.execute("INSERT INTO pg_temp.aic_instance_reservations VALUES ($1,'ACTIVE')", retired_id)
    await c.execute("SET search_path TO pg_temp, " + schema)
    try:
        await c.execute(
            "INSERT INTO " + schema + ".aic_instance_live "
            "(agent_instance_id,owner_user_id,definition_id,"
            "definition_version,lifecycle_state,generation) "
            "VALUES ($1,'alice','forged','v1','ACTIVE',1)", retired_id)
    except Exception as exc:
        if "AIC_LIVE_OWNER_OR_ID_INVALID" not in str(exc):
            print("WRONG_ERROR:" + str(exc), flush=True)
            os._exit(19)
        print("AIC31B_RETIRED_REBIND_DENIED", flush=True)
        os._exit(77)
    print("AIC31B_SECURITY_BYPASS", flush=True)
    os._exit(20)
asyncio.run(main())
"""
        dsn = postgres_url.replace("postgresql+asyncpg://", "postgresql://")
        result = subprocess.run(
            [sys.executable, "-c", worker, dsn, schema, retired_id],
            capture_output=True, text=True, timeout=40,
        )
        assert result.returncode == 77, (
            result.returncode, result.stdout, result.stderr
        )
        assert "AIC31B_RETIRED_REBIND_DENIED" in result.stdout
        async with engine.connect() as conn:
            assert (await conn.execute(text(
                "SELECT state FROM " + schema + ".aic_instance_reservations "
                "WHERE agent_instance_id=:i"
            ), {"i": retired_id})).scalar_one() == "RETIRED"
            assert (await conn.execute(text(
                "SELECT COUNT(*) FROM " + schema + ".aic_instance_live "
                "WHERE agent_instance_id=:i"
            ), {"i": retired_id})).scalar_one() == 0
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()
