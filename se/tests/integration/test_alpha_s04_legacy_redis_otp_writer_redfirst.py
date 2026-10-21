"""S04 RED-first: real Redis/PG17 legacy-to-modern writer compatibility.

The legacy protocol is the previous unfenced Redis SET wire shape. This is a
physical independent Redis client, NOT a pretend legacy process/binary. The
test checks a new-version writer cannot overwrite an already-live challenge
created without its PG17 issuance fence. Real cross-version binary/HA and
failover matrices are separate HOLDs until independently witnessed.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import redis.asyncio as redis

from se.src.infrastructure.storage.interfaces.cache import CacheWriteCollision
from se.src.infrastructure.storage.models.sql.user_data.pending_registration import PendingRegistration
from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService
from se.tests.integration.test_ae359_post_ack_loss_physical import (
    _factory,
    _pg_engines,
    _redis_driver,
    _row_count,
    _targets,
)


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_existing_legacy_redis_otp_must_survive_modern_issue_attempt(purpose):
    """Do not replace a live pre-fence OTP when a modern writer joins."""
    async def run():
        redis_url, _ = _targets()  # Fail/skip closed under the existing physical admission.
        engine = await _pg_engines()  # Attests actual PG17, not SQLite/mock.
        email = f"s04-legacy-{uuid4().hex}@example.invalid"
        new_email = f"s04-modern-positive-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown_key = (
            "password_reset_cooldown:" if reset else "otp_cooldown:"
        ) + email
        new_key = ("pending_password_reset:" if reset else "pending_reg:") + new_email
        new_cooldown_key = (
            "password_reset_cooldown:" if reset else "otp_cooldown:"
        ) + new_email
        save_name = (
            "save_pending_password_reset" if reset
            else "save_pending_registration"
        )
        verify_name = "verify_password_reset" if reset else "verify_and_get_data"
        original = json.dumps({
            "purpose": purpose,
            "otp": "111111",
            "user_data": {"value": "original-legacy"},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        old_client = redis.Redis.from_url(redis_url, decode_responses=True)
        new_driver = None
        try:
            assert await old_client.ping(), "real Redis required"
            # Exact old wire protocol: no PG17 reservation before Redis SET.
            assert await old_client.set(key, original, ex=300) is True
            assert await old_client.get(key) == original
            assert await old_client.ttl(key) > 0

            # Independent Redis client and real SQL UOW model a new writer.
            new_driver = await _redis_driver(redis_url)
            modern = OTPStorageService(new_driver, _factory(engine))
            # Never accept an arbitrary exception as a valid rejection.
            # A broken database/driver or pre-dispatch outage is NOT proof
            # that the Redis NX collision safety boundary was exercised.
            with pytest.raises(CacheWriteCollision, match="Live OTP already exists"):
                await getattr(modern, save_name)(
                    email, {"value": "modern-attempt"}, "222222"
                )

            actual = await old_client.get(key)
            assert actual == original, (
                "S04_SEMANTIC_RED: modern SET destroyed an active pre-fence "
                f"legacy OTP ({purpose})"
            )
            assert await old_client.ttl(key) > 0, (
                "S04_SEMANTIC_RED: original challenge lost its live TTL"
            )
            assert await _row_count(engine, email) == 0, (
                "S04_SEMANTIC_RED: a second SQL OTP authority was persisted"
            )

            # Positive control on the SAME physical modern driver/UOW:
            # collision rejection must not be mistaken for a broken writer.
            await getattr(modern, save_name)(
                new_email, {"value": "modern-positive"}, "333333"
            )
            assert await old_client.exists(new_key) == 1, (
                "S04_HARNESS_ERROR: modern positive control never reached Redis"
            )

            verifier = OTPStorageService(new_driver, _factory(engine))
            fresh = await getattr(verifier, verify_name)(new_email, "333333")
            assert fresh == {"value": "modern-positive"}, (
                "S04_SEMANTIC_RED: fresh modern OTP failed positive verification"
            )
            assert await getattr(verifier, verify_name)(new_email, "333333") is None, (
                "S04_SEMANTIC_RED: fresh modern OTP replayed"
            )
            first = await getattr(verifier, verify_name)(email, "111111")
            second = await getattr(verifier, verify_name)(email, "222222")
            replay = await getattr(verifier, verify_name)(email, "111111")
            assert first == {"value": "original-legacy"}, "legacy OTP lost"
            assert second is None, "S04_SEMANTIC_RED: second OTP authenticated"
            assert replay is None, "S04_SEMANTIC_RED: first OTP replayed"
        finally:
            if new_driver is not None:
                await new_driver.disconnect()
            await old_client.delete(key, cooldown_key, new_key, new_cooldown_key)
            async with engine.begin() as connection:
                await connection.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email.in_([email, new_email])
                    )
                )
            await old_client.aclose()
            await engine.dispose()

    asyncio.run(run())

@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_legacy_first_cold_redis_must_not_issue_second_sql_otp(purpose):
    """F-670-SEC-01: old Redis OTP + cold new Redis writer is NOT a SQL miss.

    Strict real Redis and PG17; the two stores are observed independently.
    This test deliberately does not accept arbitrary exceptions as denial.
    """
    from se.src.infrastructure.config.schemas import DriverConfig
    from se.src.infrastructure.storage.drivers.redis.driver import RedisDriver
    from se.src.infrastructure.storage.interfaces.cache import CacheWriteNotDispatched

    async def run():
        redis_url, _ = _targets()
        engine = await _pg_engines()
        email = f"s04-cold-legacy-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown = ("password_reset_cooldown:" if reset else "otp_cooldown:") + email
        save = "save_pending_password_reset" if reset else "save_pending_registration"
        verify = "verify_password_reset" if reset else "verify_and_get_data"
        old = redis.Redis.from_url(redis_url, decode_responses=True)
        reader = None
        try:
            assert await old.ping(), "S04_HARNESS_ERROR: physical Redis missing"
            original = json.dumps({
                "purpose": purpose, "otp": "111111",
                "user_data": {"value": "legacy"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            assert await old.set(key, original, ex=300) is True
            assert await old.get(key) == original
            assert await old.ttl(key) > 0
            assert await _row_count(engine, email) == 0

            # The modern instance's *own* SET was not dispatched, but a
            # DIFFERENT legacy instance may already have issued in Redis.
            cold = RedisDriver(DriverConfig(options={"url": redis_url}))
            assert cold._client is None
            modern = OTPStorageService(cold, _factory(engine))
            outcome = "ACKED"
            try:
                await getattr(modern, save)(email, {"value": "modern-sql"}, "222222")
            except CacheWriteNotDispatched:
                outcome = "REJECTED"
            # Any other exception is not accepted as a security denial.
            assert await old.get(key) == original, (
                "S04_SEMANTIC_RED: cold writer replaced legacy Redis OTP"
            )
            assert await old.ttl(key) > 0
            sql_count = await _row_count(engine, email)
            reader = await _redis_driver(redis_url)
            verifier = OTPStorageService(reader, _factory(engine))
            first = await getattr(verifier, verify)(email, "111111")
            second = await getattr(verifier, verify)(email, "222222")
            replay = await getattr(verifier, verify)(email, "111111")
            assert first == {"value": "legacy"}, "S04_HARNESS_ERROR: legacy positive control failed"
            assert second is None, (
                "S04_SEMANTIC_RED: live legacy Redis OTP and cold modern "
                f"SQL fallback authenticated a second OTP ({purpose}; SQL rows={sql_count})"
            )
            assert replay is None, "S04_SEMANTIC_RED: legacy OTP replayed"
            assert sql_count == 0, (
                "S04_SEMANTIC_RED: second SQL OTP authority persisted while "
                f"legacy Redis key was live ({purpose})"
            )
            assert outcome == "REJECTED", (
                "S04_SEMANTIC_RED: cold Redis issuance falsely ACKed "
                f"an independent SQL OTP ({purpose})"
            )
        finally:
            if reader is not None:
                await reader.disconnect()
            await old.delete(key, cooldown)
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await old.aclose()
            await engine.dispose()

    asyncio.run(run())
