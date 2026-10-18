"""AE359 UTC-naive PostgreSQL 17 RED-first tests — TEST ONLY.

All database writes use a disposable loopback PostgreSQL 17 database.
The tests express the intended production contract, not a workaround for it.
Cache-fallback controls use a deterministic failure *before Redis dispatch*;
post-execution Redis ACK-loss stays covered unchanged by PR #557 oracles.

Strict CI: AE359_TZ_PG17_STRICT=1 and AE359_TZ_PG17_URL to the
disposable 127.0.0.1/ae359_otp_t1 database. No production credentials.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from se.src.infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from se.src.infrastructure.storage.interfaces.cache import CacheWriteNotDispatched
from se.src.infrastructure.storage.models.sql.user_data.pending_registration import (
    PendingRegistration,
)
from se.src.infrastructure.storage.repositories.user_data.pending_registrations import (
    PendingRegistrationRepository,
)
from se.src.transport.gateway.authentication.services.otp_service import (
    OTPStorageService,
)

pytestmark = pytest.mark.asyncio

PAYLOAD = json.dumps({
    "purpose": "registration",
    "otp": "123456",
    "user_data": {"value": "synthetic-tz-only"},
})


class _DbDriver:
    def __init__(self, session_maker):
        self.session_maker = session_maker

    def get_session(self):
        return self.session_maker()


class _CacheUnavailableBeforeDispatch:
    """Control only: deterministic cache connection refusal, no Redis command sent."""
    def __init__(self):
        self.attempted_keys: list[str] = []

    async def set(self, key, value, expire):
        self.attempted_keys.append(key)
        raise CacheWriteNotDispatched("test-only: Redis unavailable BEFORE command dispatch")


def _uow_factory(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)
    driver = _DbDriver(maker)
    return lambda: SqlAlchemyUnitOfWork(driver)


async def _utc_db_now(engine):
    async with engine.connect() as connection:
        return (await connection.execute(text(
            "SELECT clock_timestamp() AT TIME ZONE 'UTC'"
        ))).scalar_one()


async def _read_by_email(engine, email):
    """Independent physical read; no uncommitted Session identity-map values."""
    async with engine.connect() as connection:
        return (await connection.execute(
            select(
                PendingRegistration.email,
                PendingRegistration.payload,
                PendingRegistration.created_at,
                PendingRegistration.expires_at,
            ).where(PendingRegistration.email == email)
        )).mappings().one_or_none()


async def _seed_naive(engine, email, *, age_seconds=0, ttl_seconds=300, payload=PAYLOAD):
    """Test-only positive fixture that already matches the column contract."""
    now = await _utc_db_now(engine)
    created_at = now - timedelta(seconds=age_seconds)
    async with engine.begin() as connection:
        await connection.execute(PendingRegistration.__table__.insert().values(
            email=email,
            payload=payload,
            created_at=created_at,
            expires_at=now + timedelta(seconds=ttl_seconds),
        ))


class _PgCase:
    def __init__(self, engine):
        self.engine = engine
        self.emails: list[str] = []

    def unique_email(self):
        email = f"ae359-tz-{uuid4().hex}@example.invalid"
        self.emails.append(email)
        return email

    async def cleanup(self):
        async with self.engine.begin() as connection:
            await connection.execute(delete(PendingRegistration).where(
                PendingRegistration.email.in_(self.emails)
            ))


@pytest_asyncio.fixture
async def pg17():
    if os.environ.get("AE359_TZ_PG17_STRICT") != "1":
        pytest.fail("STRICT physical PG17 admission required; no skip is allowed")
    dsn = os.environ.get("AE359_TZ_PG17_URL")
    if not dsn:
        pytest.fail("AE359_TZ_PG17_URL missing")
    u = make_url(dsn)
    if (u.drivername != "postgresql+asyncpg"
            or u.host != "127.0.0.1"
            or u.database != "ae359_otp_t1"):
        pytest.fail("Only disposable PostgreSQL on 127.0.0.1/ae359_otp_t1 is accepted")

    engine = create_async_engine(u, connect_args={"ssl": False}, pool_pre_ping=True)
    case = _PgCase(engine)
    try:
        async with engine.begin() as conn:
            version = int((await conn.execute(text(
                "SELECT current_setting('server_version_num')"
            ))).scalar_one())
            assert 170000 <= version < 180000, "A real PostgreSQL 17 server is required"
            await conn.run_sync(PendingRegistration.__table__.create, checkfirst=True)
            schema = (await conn.execute(text("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'pending_registrations'
                  AND column_name IN ('created_at', 'expires_at')
            """))).all()
            assert dict(schema) == {
                "created_at": "timestamp without time zone",
                "expires_at": "timestamp without time zone",
            }, f"Unexpected physical timestamp schema: {schema!r}"
        yield case
    finally:
        try:
            await case.cleanup()
        finally:
            await engine.dispose()


async def test_tz01_real_pg17_repository_create_persists_utc_naive_pair(pg17):
    email = pg17.unique_email()
    maker = async_sessionmaker(pg17.engine, expire_on_commit=False)
    async with maker() as session:
        repo = PendingRegistrationRepository(session)
        await repo.create_or_update(email, PAYLOAD, 300)
        await session.commit()
    row = await _read_by_email(pg17.engine, email)
    assert row is not None
    assert row["created_at"].tzinfo is None
    assert row["expires_at"].tzinfo is None
    assert row["expires_at"] - row["created_at"] == timedelta(seconds=300)
    db_now = await _utc_db_now(pg17.engine)
    assert 0 <= (db_now - row["created_at"]).total_seconds() < 20


async def test_tz02_real_pg17_repository_update_replaces_existing_row(pg17):
    email = pg17.unique_email()
    await _seed_naive(pg17.engine, email, ttl_seconds=30)
    updated_payload = json.dumps({
        "purpose": "registration", "otp": "789012",
        "user_data": {"value": "synthetic-updated"},
    })
    maker = async_sessionmaker(pg17.engine, expire_on_commit=False)
    async with maker() as session:
        repo = PendingRegistrationRepository(session)
        await repo.create_or_update(email, updated_payload, 120)
        await session.commit()
    row = await _read_by_email(pg17.engine, email)
    assert row is not None and row["payload"] == updated_payload
    assert row["created_at"].tzinfo is None
    assert row["expires_at"] - row["created_at"] == timedelta(seconds=120)
    async with pg17.engine.connect() as conn:
        count = (await conn.execute(text(
            "SELECT count(*) FROM pending_registrations WHERE email=:email"
        ), {"email": email})).scalar_one()
    assert count == 1


async def test_tz03_real_pg17_model_default_created_at_is_utc_naive(pg17):
    email = pg17.unique_email()
    now = await _utc_db_now(pg17.engine)
    maker = async_sessionmaker(pg17.engine, expire_on_commit=False)
    async with maker() as session:
        session.add(PendingRegistration(
            email=email, payload=PAYLOAD,
            expires_at=now + timedelta(seconds=90)
        ))
        await session.commit()
    row = await _read_by_email(pg17.engine, email)
    assert row is not None and row["created_at"].tzinfo is None
    assert row["expires_at"] - row["created_at"] >= timedelta(seconds=85)


async def test_tz04_real_pg17_cleanup_removes_expired_preserves_live(pg17):
    expired, live = pg17.unique_email(), pg17.unique_email()
    await _seed_naive(pg17.engine, expired, ttl_seconds=-10)
    await _seed_naive(pg17.engine, live, ttl_seconds=300)
    maker = async_sessionmaker(pg17.engine, expire_on_commit=False)
    async with maker() as session:
        repo = PendingRegistrationRepository(session)
        await repo.cleanup_expired()
        await session.commit()
    assert await _read_by_email(pg17.engine, expired) is None
    assert await _read_by_email(pg17.engine, live) is not None


async def test_tz05_real_pg17_cooldown_uses_utc_naive_created_at(pg17):
    email = pg17.unique_email()
    await _seed_naive(pg17.engine, email, age_seconds=10, ttl_seconds=180)
    maker = async_sessionmaker(pg17.engine, expire_on_commit=False)
    async with maker() as session:
        value = await PendingRegistrationRepository(
            session
        ).get_remaining_cooldown(email, 60)
    assert 47 <= value <= 50, f"UTC cooldown should be close to 50s, got {value}"


@pytest.mark.parametrize(
    "flow,purpose,prefix",
    [
        ("registration", "registration", "pending_reg:"),
        ("password_reset", "password_reset", "pending_password_reset:"),
    ],
    ids=["tz06-registration-fallback", "tz07-password-reset-fallback"],
)
async def test_tz06_tz07_real_pg17_definite_cache_non_dispatch_fallback(
    pg17, flow, purpose, prefix
):
    email = pg17.unique_email()
    cache = _CacheUnavailableBeforeDispatch()
    service = OTPStorageService(cache, _uow_factory(pg17.engine))
    if flow == "registration":
        await service.save_pending_registration(
            email, {"value": "synthetic-tz-only"}, "123456"
        )
    else:
        await service.save_pending_password_reset(
            email, {"value": "synthetic-tz-only"}, "123456"
        )
    assert cache.attempted_keys == [prefix + email]
    row = await _read_by_email(pg17.engine, email)
    assert row is not None
    data = json.loads(row["payload"])
    assert data["purpose"] == purpose
    assert data["otp"] == "123456"
    assert row["created_at"].tzinfo is None
    assert row["expires_at"].tzinfo is None
    assert row["expires_at"] - row["created_at"] == timedelta(seconds=300)


async def test_tz08_real_pg17_non_utc_session_keeps_absolute_utc_values(pg17):
    email = pg17.unique_email()
    original_url = make_url(os.environ["AE359_TZ_PG17_URL"])
    other_tz_engine = create_async_engine(
        original_url,
        connect_args={"ssl": False, "server_settings": {"TimeZone": "Pacific/Honolulu"}},
        pool_pre_ping=True,
    )
    try:
        async with other_tz_engine.connect() as conn:
            actual = (await conn.execute(text("SHOW TimeZone"))).scalar_one()
        assert actual == "Pacific/Honolulu", "Timezone fixture not physically established"
        maker = async_sessionmaker(other_tz_engine, expire_on_commit=False)
        async with maker() as session:
            await PendingRegistrationRepository(session).create_or_update(
                email, PAYLOAD, 300
            )
            await session.commit()
        row = await _read_by_email(pg17.engine, email)
        assert row is not None and row["created_at"].tzinfo is None
        utc_now = await _utc_db_now(pg17.engine)
        assert abs((utc_now - row["created_at"]).total_seconds()) < 20
        assert row["expires_at"] - row["created_at"] == timedelta(seconds=300)
    finally:
        await other_tz_engine.dispose()


async def test_tz09_real_pg17_naive_fixture_and_one_time_consume_positive_control(pg17):
    email = pg17.unique_email()
    await _seed_naive(pg17.engine, email)
    service = OTPStorageService(cache_driver=None, uow_factory=_uow_factory(pg17.engine))
    first = await service.verify_and_get_data(email, "123456")
    second = await service.verify_and_get_data(email, "123456")
    assert first == {"value": "synthetic-tz-only"}
    assert second is None
    assert await _read_by_email(pg17.engine, email) is None
