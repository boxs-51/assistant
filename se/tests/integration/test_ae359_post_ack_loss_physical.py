"""AE359 physical post-execution / pre-delivery ACK-loss tests (RED-first).

Requires *real* disposable Redis and PostgreSQL 17 listening on loopback.
The injected fault is after a backend success frame has been captured, never
an exception-only monkeypatch. Database observer uses a distinct connection.
No production services, secrets or credentials may be supplied to this suite.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import redis.asyncio as redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.drivers.redis.driver import RedisDriver
from se.src.infrastructure.storage.models.sql.user_data.pending_registration import PendingRegistration
from se.src.infrastructure.storage.repositories.refresh_sessions_redis import RedisRefreshSessionStore
from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService
from se.tests.integration.fixtures.ae359_redis_resp_ack_cut import RespAckCutProxy
from se.tests.integration.fixtures.ae359_pgwire_ack_cut import PgwireCommitAckCut


def _targets():
    if os.getenv("AE359_POSTACK_STRICT") != "1":
        pytest.skip("Physical Redis/PG17 opt-in only; strict CI must set admission")
    ru = os.environ["AE359_POSTACK_REDIS_URL"]
    pg = make_url(os.environ["AE359_POSTACK_PG_URL"])
    redis_url = redis.Redis.from_url(ru).connection_pool.connection_kwargs
    if (redis_url.get("host") != "127.0.0.1"
            or redis_url.get("db") != 15
            or redis_url.get("password") is not None):
        pytest.fail("Redis must be disposable, credential-free 127.0.0.1 DB 15")
    if (pg.host != "127.0.0.1" or pg.database != "ae359_otp_t1"
            or pg.drivername != "postgresql+asyncpg"):
        pytest.fail("PostgreSQL must be disposable 127.0.0.1/ae359_otp_t1")
    return ru, pg


class _DbDriver:
    def __init__(self, maker):
        self.maker = maker

    def get_session(self):
        return self.maker()


class _ReceiptUOW(SqlAlchemyUnitOfWork):
    def __init__(self, driver, attempt):
        super().__init__(driver)
        self.attempt = attempt

    async def commit(self):
        # Same physical transaction as production OTP DELETE, no synthetic ACK.
        await self.session.execute(
            text("INSERT INTO ae359_postack_receipts(attempt) VALUES (:attempt)"),
            {"attempt": self.attempt},
        )
        await super().commit()


async def _pg_engines():
    _, pg = _targets()
    engine = create_async_engine(pg, connect_args={"ssl": False})
    async with engine.begin() as conn:
        version = (await conn.execute(text("SHOW server_version_num"))).scalar_one()
        assert 170000 <= int(version) < 180000, "Physical PG17 required"
        await conn.run_sync(PendingRegistration.__table__.create, checkfirst=True)
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS ae359_postack_receipts "
            "(attempt TEXT PRIMARY KEY)"
        ))
    return engine


async def _redis_driver(url):
    driver = RedisDriver(DriverConfig(options={"url": url}))
    await driver.connect()
    return driver


def _factory(engine, *, attempt=None):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    driver = _DbDriver(maker)
    if attempt is not None:
        return lambda: _ReceiptUOW(driver, attempt)
    return lambda: SqlAlchemyUnitOfWork(driver)


async def _seed_otp(engine, email, otp="123456"):
    payload = json.dumps({
        "purpose": "registration", "otp": otp,
        "user_data": {"value": "synthetic-only"},
    })
    # The existing SQL model stores UTC-naive TIMESTAMP WITHOUT TIME ZONE.
    # This test fixture must seed the same physical representation as PG17 T1.
    utc_naive_now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with engine.begin() as conn:
        await conn.execute(PendingRegistration.__table__.insert().values(
            email=email, payload=payload,
            expires_at=utc_naive_now + timedelta(minutes=5),
            created_at=utc_naive_now,
        ))
    return payload


async def _row_count(engine, email):
    async with engine.connect() as conn:
        return (await conn.execute(
            select(PendingRegistration.email).where(PendingRegistration.email == email)
        )).all().__len__()


async def _finish(task):
    try:
        value = await asyncio.wait_for(task, timeout=8)
        return ("SUCCESS", value)
    except Exception as exc:
        return ("ERROR", type(exc).__name__)


def test_r_ack_01_x_ack_09_real_redis_set_ack_lost_db_dual_authority_red_first():
    """A successful Redis SET must not create a second independently usable DB OTP."""
    async def run():
        ru, _ = _targets()
        engine = await _pg_engines()
        email = "ae359-" + uuid4().hex + "@example.invalid"
        redis_key = "pending_reg:" + email
        direct = redis.Redis.from_url(ru, decode_responses=True)
        try:
            assert await direct.ping()
            async with RespAckCutProxy() as proxy:
                writer = await _redis_driver(f"redis://127.0.0.1:{proxy.port}/15")
                service = OTPStorageService(writer, _factory(engine))
                proxy.arm_once()
                task = asyncio.create_task(service.save_pending_registration(
                    email, {"value": "synthetic-only"}, "123456"
                ))
                await proxy.wait_captured()
                assert proxy.client_ack_bytes == 0
                assert await direct.exists(redis_key) == 1, "Real SET did not execute"
                proxy.release_drop.set()
                status, detail = await _finish(task)
                assert status == "ERROR", (
                    f"UNCERTAIN Redis SET must fail closed, got {status}: {detail}"
                )
                await writer.disconnect()

            assert await _row_count(engine, email) == 0, (
                "SEMANTIC_RED: uncertain Redis SET created a second SQL OTP authority"
            )
            reader = await _redis_driver(ru)
            try:
                verifier = OTPStorageService(reader, _factory(engine))
                first = await verifier.verify_and_get_data(email, "123456")
                second = await verifier.verify_and_get_data(email, "123456")
                assert first == {"value": "synthetic-only"}
                # RED-FIRST: fails if the same challenge was accepted again.
                assert second is None, "SEMANTIC_RED: one OTP authenticated twice"
            finally:
                await reader.disconnect()
        finally:
            await direct.delete(redis_key)
            async with engine.begin() as conn:
                await conn.execute(PendingRegistration.__table__.delete().where(
                    PendingRegistration.email == email
                ))
            await direct.aclose()
            await engine.dispose()
    asyncio.run(run())



def test_r_ack_02_real_password_reset_set_ack_loss_must_not_duplicate():
    """A real password-reset SET followed by lost ACK never issues DB OTP."""
    async def run():
        ru, _ = _targets()
        engine = await _pg_engines()
        email = "ae359-reset-" + uuid4().hex + "@example.invalid"
        redis_key = "pending_password_reset:" + email
        direct = redis.Redis.from_url(ru, decode_responses=True)
        try:
            assert await direct.ping()
            async with RespAckCutProxy() as proxy:
                writer = await _redis_driver(f"redis://127.0.0.1:{proxy.port}/15")
                service = OTPStorageService(writer, _factory(engine))
                proxy.arm_once()
                task = asyncio.create_task(service.save_pending_password_reset(
                    email, {"value": "synthetic-reset-only"}, "123456"
                ))
                await proxy.wait_captured()
                assert proxy.client_ack_bytes == 0
                assert await direct.exists(redis_key) == 1, "Physical reset SET missing"
                proxy.release_drop.set()
                status, detail = await _finish(task)
                assert status == "ERROR", (
                    f"Password-reset UNCERTAIN SET must fail closed: {status} {detail}"
                )
                await writer.disconnect()
            assert await _row_count(engine, email) == 0, (
                "SEMANTIC_RED: password-reset cache uncertainty duplicated into SQL"
            )
            reader = await _redis_driver(ru)
            try:
                verifier = OTPStorageService(reader, _factory(engine))
                first = await verifier.verify_password_reset(email, "123456")
                second = await verifier.verify_password_reset(email, "123456")
                assert first == {"value": "synthetic-reset-only"}
                assert second is None, "SEMANTIC_RED: password-reset OTP reused"
            finally:
                await reader.disconnect()
        finally:
            await direct.delete(redis_key)
            async with engine.begin() as conn:
                await conn.execute(PendingRegistration.__table__.delete().where(
                    PendingRegistration.email == email
                ))
            await direct.aclose()
            await engine.dispose()
    asyncio.run(run())


def test_r_ack_03_real_getdel_ack_lost_never_falls_back_to_db():
    async def run():
        ru, _ = _targets()
        engine = await _pg_engines()
        email = "ae359-" + uuid4().hex + "@example.invalid"
        key = "pending_reg:" + email
        direct = redis.Redis.from_url(ru, decode_responses=True)
        try:
            payload = await _seed_otp(engine, email)
            await direct.set(key, payload, ex=60)
            async with RespAckCutProxy() as proxy:
                writer = await _redis_driver(f"redis://127.0.0.1:{proxy.port}/15")
                service = OTPStorageService(writer, _factory(engine))
                proxy.arm_once()
                task = asyncio.create_task(service.verify_and_get_data(email, "123456"))
                await proxy.wait_captured()
                assert await direct.exists(key) == 0, "Physical GETDEL did not consume key"
                proxy.release_drop.set()
                status, _ = await _finish(task)
                assert status == "ERROR", "SEMANTIC_RED: uncertain GETDEL exposed OTP"
                assert await _row_count(engine, email) == 1, "DB fallback used after uncertain Redis consume"
                await writer.disconnect()
        finally:
            await direct.delete(key)
            async with engine.begin() as conn:
                await conn.execute(PendingRegistration.__table__.delete().where(
                    PendingRegistration.email == email
                ))
            await direct.aclose()
            await engine.dispose()
    asyncio.run(run())


def test_r_ack_04_real_v2_revoke_ack_lost_never_returns_success():
    async def run():
        ru, _ = _targets()
        direct = await _redis_driver(ru)
        token = "ae359-" + uuid4().hex
        key = RedisRefreshSessionStore.PREFIX + token
        try:
            async with RespAckCutProxy() as proxy:
                driver = await _redis_driver(f"redis://127.0.0.1:{proxy.port}/15")
                store = RedisRefreshSessionStore(driver)
                assert await store.issue(token, "user-test", "jti-test", 45) is True
                proxy.arm_once()
                task = asyncio.create_task(store.revoke(token, "user-test", "jti-test"))
                await proxy.wait_captured()
                raw = await direct._require_client().get(key)
                assert raw and json.loads(raw)["state"] == "REVOKED"
                proxy.release_drop.set()
                status, _ = await _finish(task)
                assert status == "ERROR", "SEMANTIC_RED: falsely confirmed Redis revoke"
                observer = RedisRefreshSessionStore(direct)
                assert await observer.begin(token, "user-test", "jti-test") is None
                await store.close()
                await driver.disconnect()
        finally:
            await direct._require_client().delete(key)
            await direct.disconnect()
    asyncio.run(run())


def test_pg08_p_real_pgwire_commit_ack_lost_service_must_not_return_payload():
    async def run():
        _, pg = _targets()
        direct = await _pg_engines()
        email = "ae359-" + uuid4().hex + "@example.invalid"
        attempt = "ack-" + uuid4().hex
        try:
            await _seed_otp(direct, email)
            async with PgwireCommitAckCut() as proxy:
                proxied_pg = pg.set(port=proxy.port)
                writer_engine = create_async_engine(
                    proxied_pg, connect_args={"ssl": False}, pool_size=1
                )
                try:
                    # Warm up asyncpg initialization before arming the COMMIT cut.
                    async with writer_engine.connect() as conn:
                        await conn.execute(text("SELECT 1"))
                    service = OTPStorageService(
                        None, _factory(writer_engine, attempt=attempt)
                    )
                    proxy.arm_once()
                    task = asyncio.create_task(service.verify_and_get_data(
                        email, "123456"
                    ))
                    await proxy.wait_captured()
                    assert proxy.client_commit_ack_bytes == 0
                    async with direct.connect() as observer:
                        receipts = (await observer.execute(
                            text("SELECT count(*) FROM ae359_postack_receipts "
                                 "WHERE attempt=:id"), {"id": attempt}
                        )).scalar_one()
                    assert receipts == 1, "COMMIT not physically visible to observer"
                    assert await _row_count(direct, email) == 0
                    proxy.release_drop.set()
                    status, _ = await _finish(task)
                    assert status == "ERROR", "SEMANTIC_RED: OTP payload escaped without COMMIT ACK"
                finally:
                    await writer_engine.dispose()
        finally:
            async with direct.begin() as conn:
                await conn.execute(PendingRegistration.__table__.delete().where(
                    PendingRegistration.email == email
                ))
                await conn.execute(
                    text("DELETE FROM ae359_postack_receipts WHERE attempt=:id"),
                    {"id": attempt},
                )
            await direct.dispose()
    asyncio.run(run())


def test_ctrl_10_real_redis_and_postgres_confirmed_ack_controls():
    async def run():
        ru, _ = _targets()
        direct = redis.Redis.from_url(ru, decode_responses=True)
        engine = await _pg_engines()
        email = "ae359-" + uuid4().hex + "@example.invalid"
        try:
            async with RespAckCutProxy() as proxy:
                driver = await _redis_driver(f"redis://127.0.0.1:{proxy.port}/15")
                try:
                    service = OTPStorageService(driver, _factory(engine))
                    await service.save_pending_registration(email, {"value": "control"}, "123456")
                    assert await service.verify_and_get_data(email, "123456") == {"value": "control"}
                    assert await service.verify_and_get_data(email, "123456") is None
                    assert not proxy.captured.is_set(), "Control must not inject network fault"
                finally:
                    await driver.disconnect()
        finally:
            await direct.delete("pending_reg:" + email, "otp_cooldown:" + email)
            await direct.aclose()
            await engine.dispose()
    asyncio.run(run())
