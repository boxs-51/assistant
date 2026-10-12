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
        assert await read_scalar("SELECT version_num FROM alembic_version") == revision

    try:
        # Genuine PostgreSQL bootstrap is blocked before 25a by the
        # historical alembic_version VARCHAR(32) limit at long revision 5b.
        # Both real PG17 Linux and Windows reproduced the failure on the
        # untouched fresh DB (evidence #38260235391). The separate scoped
        # compatibility run BELOW widens ONLY version bookkeeping on this
        # disposable database; it does NOT certify unprepared production
        # upgrades or modify any historical Alembic revision.
        if backend == "postgresql":
            _aic_run_real_alembic(url, "896c456631dd")
            await require_revision("896c456631dd")
            async with engine.begin() as cx:
                await cx.execute(text(
                    "ALTER TABLE alembic_version "
                    "ALTER COLUMN version_num TYPE VARCHAR(255)"
                ))
        # Execute the actual 5b...24a ancestors (no Alembic stamp/fake DDL).
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
    finally:
        await engine.dispose()
        if admin is not None:
            async with admin.connect() as c:
                await c.execute(text("DROP DATABASE IF EXISTS " + db_name + " WITH (FORCE)"))
            await admin.dispose()
