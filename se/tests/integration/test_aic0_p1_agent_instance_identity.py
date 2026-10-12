"""AIC-0-P1A genuine SQLite FK-OFF and optional native PostgreSQL tests.

No fake UoW/session, no hidden second commit, and no runtime/API activation.
Set AIC_TEST_POSTGRES_URL to a disposable PostgreSQL DB to run the PG lock test.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
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
        await conn.run_sync(AgentInstanceReservationRecord.__table__.create)
        await conn.run_sync(AgentInstanceLiveRecord.__table__.create)
        guards = MIG._SQLITE_GUARDS if dialect == "sqlite" else (
            MIG._PG_FUNCTIONS + MIG._PG_TRIGGERS
        )
        for sql in guards:
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


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("AIC_TEST_POSTGRES_URL"),
                    reason="real PostgreSQL requires AIC_TEST_POSTGRES_URL")
async def test_aic_native_postgres_nonkey_status_update_lock():
    # Real two-session row lock; no fake dialect or SQLite-as-PG substitution.
    url = os.environ["AIC_TEST_POSTGRES_URL"]
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
            with pytest.raises(DatabaseError):
                await updater.execute(
                    text("UPDATE users SET status='inactive' WHERE id='alice'")
                )
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
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()
