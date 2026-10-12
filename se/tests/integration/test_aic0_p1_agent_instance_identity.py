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
    ["sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://"],
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
