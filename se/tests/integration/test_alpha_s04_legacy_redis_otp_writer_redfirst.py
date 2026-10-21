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

# Mixed-version/admission questions are separately scoped from the
# F-670-SEC-01 fix above. These are real Redis/PG17 semantic RED-first tests:
# modern DB-only + older Redis, and old unconditional SET after new NX.
@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_mixed_db_only_with_live_legacy_must_not_double_redeem(purpose):
    """DB-only modern issuance cannot mint a second OTP alongside legacy Redis."""
    async def run():
        redis_url, _ = _targets()
        engine = await _pg_engines()
        email = f"s04-mixed-db-only-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown = ("password_reset_cooldown:" if reset else "otp_cooldown:") + email
        save = "save_pending_password_reset" if reset else "save_pending_registration"
        verify = "verify_password_reset" if reset else "verify_and_get_data"
        legacy = redis.Redis.from_url(redis_url, decode_responses=True)
        reader = None
        try:
            assert await legacy.ping(), "S04_HARNESS_ERROR: Redis unavailable"
            old = json.dumps({
                "purpose": purpose, "otp": "111111",
                "user_data": {"value": "legacy"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            assert await legacy.set(key, old, ex=300) is True
            assert await legacy.get(key) == old
            assert await legacy.ttl(key) > 0
            assert await _row_count(engine, email) == 0

            modern_db_only = OTPStorageService(None, _factory(engine))
            # A real SQL-only deployment used alongside a Redis-backed old
            # binary is not proven to own the sole issuance authority.
            await getattr(modern_db_only, save)(
                email, {"value": "db-only-modern"}, "222222"
            )
            rows = await _row_count(engine, email)
            reader = await _redis_driver(redis_url)
            verifier = OTPStorageService(reader, _factory(engine))
            original = await getattr(verifier, verify)(email, "111111")
            second = await getattr(verifier, verify)(email, "222222")
            replay = await getattr(verifier, verify)(email, "111111")
            assert original == {"value": "legacy"}, (
                "S04_HARNESS_ERROR: legacy OTP positive control unavailable"
            )
            assert second is None, (
                "S04_MIXED_SEMANTIC_RED: DB-only modern and Redis legacy "
                f"issued separately redeemable OTPs ({purpose}; sql_rows={rows})"
            )
            assert replay is None, "S04_MIXED_SEMANTIC_RED: legacy OTP replayed"
            assert rows == 0, (
                "S04_MIXED_SEMANTIC_RED: a second SQL authority was committed "
                f"while unfenced legacy Redis was live ({purpose})"
            )
        finally:
            if reader is not None:
                await reader.disconnect()
            await legacy.delete(key, cooldown)
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await legacy.aclose()
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_mixed_legacy_late_set_must_not_replace_modern_ack(purpose):
    """An old unconditional Redis SET must not invalidate acknowledged modern NX."""
    async def run():
        redis_url, _ = _targets()
        engine = await _pg_engines()
        email = f"s04-mixed-late-old-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown = ("password_reset_cooldown:" if reset else "otp_cooldown:") + email
        save = "save_pending_password_reset" if reset else "save_pending_registration"
        verify = "verify_password_reset" if reset else "verify_and_get_data"
        old_writer = redis.Redis.from_url(redis_url, decode_responses=True)
        modern_driver = None
        try:
            assert await old_writer.ping(), "S04_HARNESS_ERROR: Redis unavailable"
            modern_driver = await _redis_driver(redis_url)
            modern = OTPStorageService(modern_driver, _factory(engine))
            await getattr(modern, save)(
                email, {"value": "modern-acknowledged"}, "222222"
            )
            modern_value = await old_writer.get(key)
            assert modern_value is not None, (
                "S04_HARNESS_ERROR: modern positive did not create OTP"
            )
            assert json.loads(modern_value)["otp"] == "222222"
            assert await old_writer.ttl(key) > 0

            # Genuine old Redis wire command: no SQL fence, no NX flag.
            # This is NOT a real historical Python binary.
            legacy_payload = json.dumps({
                "purpose": purpose, "otp": "111111",
                "user_data": {"value": "old-late-writer"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            assert await old_writer.set(key, legacy_payload, ex=300) is True
            observed_after = await old_writer.get(key)
            assert observed_after == modern_value, (
                "S04_MIXED_SEMANTIC_RED: legacy SET overwrote a live "
                f"acknowledged modern OTP ({purpose})"
            )
            verifier = OTPStorageService(modern_driver, _factory(engine))
            assert await getattr(verifier, verify)(email, "222222") == {
                "value": "modern-acknowledged"
            }, "S04_MIXED_SEMANTIC_RED: modern acknowledged OTP was invalidated"
            assert await getattr(verifier, verify)(email, "111111") is None, (
                "S04_MIXED_SEMANTIC_RED: late legacy OTP gained authority"
            )
        finally:
            if modern_driver is not None:
                await modern_driver.disconnect()
            await old_writer.delete(key, cooldown)
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await old_writer.aclose()
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_mixed_isolated_sql_only_positive_control(purpose):
    """Healthy DB-only deployment remains useful, but is not mixed-mode proof."""
    async def run():
        _, _ = _targets()
        engine = await _pg_engines()
        email = f"s04-mixed-sql-control-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        save = "save_pending_password_reset" if reset else "save_pending_registration"
        verify = "verify_password_reset" if reset else "verify_and_get_data"
        try:
            sql_only = OTPStorageService(None, _factory(engine))
            await getattr(sql_only, save)(
                email, {"value": "sql-only-control"}, "333333"
            )
            assert await _row_count(engine, email) == 1
            assert await getattr(sql_only, verify)(email, "333333") == {
                "value": "sql-only-control"
            }
            assert await getattr(sql_only, verify)(email, "333333") is None
            assert await _row_count(engine, email) == 0
        finally:
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_mixed_inmemory_modern_with_live_legacy_must_not_double_redeem(purpose):
    """A process-local modern OTP is not exclusive while old Redis OTP lives."""
    from se.src.infrastructure.config.schemas import DriverConfig
    from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver

    async def run():
        redis_url, _ = _targets()
        engine = await _pg_engines()
        email = f"s04-mixed-memory-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown = ("password_reset_cooldown:" if reset else "otp_cooldown:") + email
        save = "save_pending_password_reset" if reset else "save_pending_registration"
        verify = "verify_password_reset" if reset else "verify_and_get_data"
        old = redis.Redis.from_url(redis_url, decode_responses=True)
        redis_reader = None
        memory = InMemoryDriver(DriverConfig(options={}))
        try:
            assert await old.ping(), "S04_HARNESS_ERROR: Redis unavailable"
            old_payload = json.dumps({
                "purpose": purpose, "otp": "111111",
                "user_data": {"value": "legacy-redis"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            assert await old.set(key, old_payload, ex=300) is True
            assert await old.get(key) == old_payload
            assert await old.ttl(key) > 0
            assert await _row_count(engine, email) == 0

            await memory.connect()
            modern_inmemory = OTPStorageService(memory, _factory(engine))
            await getattr(modern_inmemory, save)(
                email, {"value": "modern-inmemory"}, "222222"
            )
            assert await old.get(key) == old_payload, (
                "S04_HARNESS_ERROR: legacy Redis fixture was unexpectedly modified"
            )
            redis_reader = await _redis_driver(redis_url)
            old_verifier = OTPStorageService(redis_reader, _factory(engine))
            original = await getattr(old_verifier, verify)(email, "111111")
            second = await getattr(modern_inmemory, verify)(email, "222222")
            replay = await getattr(modern_inmemory, verify)(email, "222222")
            assert original == {"value": "legacy-redis"}, (
                "S04_HARNESS_ERROR: legacy OTP positive control failed"
            )
            assert second is None, (
                "S04_MIXED_SEMANTIC_RED: legacy Redis and modern InMemory "
                f"accepted distinct OTPs for same principal/purpose ({purpose})"
            )
            assert replay is None, "S04_MIXED_SEMANTIC_RED: memory OTP replayed"
        finally:
            if redis_reader is not None:
                await redis_reader.disconnect()
            await memory.disconnect()
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


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_mixed_isolated_inmemory_positive_control(purpose):
    """Single process in-memory one-time behavior remains a healthy control."""
    from se.src.infrastructure.config.schemas import DriverConfig
    from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver

    async def run():
        _targets()
        engine = await _pg_engines()
        email = f"s04-mixed-memory-control-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        save = "save_pending_password_reset" if reset else "save_pending_registration"
        verify = "verify_password_reset" if reset else "verify_and_get_data"
        memory = InMemoryDriver(DriverConfig(options={}))
        try:
            await memory.connect()
            service = OTPStorageService(memory, _factory(engine))
            await getattr(service, save)(
                email, {"value": "memory-only-control"}, "333333"
            )
            assert await getattr(service, verify)(email, "333333") == {
                "value": "memory-only-control"
            }
            assert await getattr(service, verify)(email, "333333") is None
        finally:
            await memory.disconnect()
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_acl_distinct_writer_epoch_denies_old_set(purpose):
    """Physical Redis 7.4 ACL cutover, not a deployment approval."""
    from redis.exceptions import ResponseError
    from se.src.infrastructure.storage.interfaces.cache import CacheWriteCollision

    async def run():
        url, _ = _targets()
        engine = await _pg_engines()
        admin = redis.Redis.from_url(url, decode_responses=True)
        uid = uuid4().hex[:16]
        old_user, new_user = "s04_old_" + uid, "s04_new_" + uid
        old_password, new_password = uuid4().hex, uuid4().hex
        email = f"s04-acl-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cool = ("password_reset_cooldown:" if reset else "otp_cooldown:") + email
        save = "save_pending_password_reset" if reset else "save_pending_registration"
        verify = "verify_password_reset" if reset else "verify_and_get_data"
        old = None
        modern_driver = None
        created = []
        try:
            assert await admin.ping()
            assert str((await admin.info("server"))["redis_version"]).startswith("7.4.")
            assert await admin.execute_command("ACL", "WHOAMI") == "default"
            assert await admin.execute_command(
                "ACL", "SETUSER", old_user, "reset", "on", ">" + old_password,
                "~pending_reg:*", "~pending_password_reset:*",
                "+ping", "+get", "+set", "+ttl", "+select"
            )
            created.append(old_user)
            assert await admin.execute_command(
                "ACL", "SETUSER", new_user, "reset", "on", ">" + new_password,
                "~pending_reg:*", "~pending_password_reset:*",
                "~otp_cooldown:*", "~password_reset_cooldown:*",
                "+ping", "+get", "+getdel", "+set", "+ttl", "+select"
            )
            created.append(new_user)
            old = redis.Redis(host="127.0.0.1", port=6379, db=15,
                              username=old_user, password=old_password,
                              decode_responses=True)
            assert await old.ping()
            legacy_value = json.dumps({
                "purpose": purpose, "otp": "111111",
                "user_data": {"value": "old"},
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            assert await old.set(key, legacy_value, ex=300) is True
            modern_url = ("redis://" + new_user + ":" + new_password
                          + "@127.0.0.1:6379/15")
            modern_driver = await _redis_driver(modern_url)
            with pytest.raises(CacheWriteCollision):
                await modern_driver.set(key, "new", 300)
            assert await admin.get(key) == legacy_value
            # An independent admin revokes the old writer before issuing new.
            assert await admin.execute_command(
                "ACL", "SETUSER", old_user, "reset", "on",
                ">" + old_password, "~pending_reg:*", "~pending_password_reset:*",
                "+ping", "+get", "+ttl", "+select"
            )
            for command in (
                ("SET", key, "late", "EX", 300),
                ("GETSET", key, "late"),
                ("DEL", key),
                ("EVAL", "return 1", 0),
                ("ACL", "SETUSER", old_user, "+set"),
            ):
                with pytest.raises(ResponseError, match="(?i)permission"):
                    await old.execute_command(*command)
            assert await admin.get(key) == legacy_value
            # External drain: never just flush/delete old OTP without receipt.
            assert await admin.getdel(key) == legacy_value
            assert await admin.get(key) is None
            modern = OTPStorageService(modern_driver, _factory(engine))
            await getattr(modern, save)(email, {"value": "modern"}, "222222")
            assert json.loads(await admin.get(key))["otp"] == "222222"
            with pytest.raises(ResponseError, match="(?i)permission"):
                await old.set(key, "rollback-attempt", ex=300)
            verifier = OTPStorageService(modern_driver, _factory(engine))
            assert await getattr(verifier, verify)(email, "222222") == {
                "value": "modern"
            }
            assert await getattr(verifier, verify)(email, "222222") is None
            assert await getattr(verifier, verify)(email, "111111") is None
            assert await _row_count(engine, email) == 0
        finally:
            if modern_driver is not None:
                await modern_driver.disconnect()
            if old is not None:
                await old.aclose()
            await admin.delete(key, cool)
            if created:
                await admin.execute_command("ACL", "DELUSER", *created)
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await admin.aclose()
            await engine.dispose()
    asyncio.run(run())


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_s04_acl_shared_credentials_rollback_hazard_control(purpose):
    """Expected hazard: a shared Redis ACL identity cannot distinguish writers."""
    from redis.exceptions import ResponseError

    async def run():
        url, _ = _targets()
        engine = await _pg_engines()
        admin = redis.Redis.from_url(url, decode_responses=True)
        name = "s04_shared_" + uuid4().hex[:16]
        password = uuid4().hex
        email = f"s04-acl-shared-{uuid4().hex}@example.invalid"
        key = ("pending_password_reset:" if purpose == "password_reset"
               else "pending_reg:") + email
        old, modern = None, None
        created = False
        try:
            assert str((await admin.info("server"))["redis_version"]).startswith("7.4.")
            assert await admin.execute_command(
                "ACL", "SETUSER", name, "reset", "on", ">" + password,
                "~pending_reg:*", "~pending_password_reset:*",
                "+ping", "+get", "+set", "+select"
            )
            created = True
            old = redis.Redis(host="127.0.0.1", port=6379, db=15,
                              username=name, password=password, decode_responses=True)
            modern = redis.Redis(host="127.0.0.1", port=6379, db=15,
                                 username=name, password=password, decode_responses=True)
            assert await modern.set(key, "modern", ex=300, nx=True) is True
            assert await old.set(key, "old-unconditional", ex=300) is True
            assert await admin.get(key) == "old-unconditional", (
                "S04_ACL_HARNESS_ERROR: shared credentials hazard absent"
            )
            # Revoking shared SET blocks both binaries. Restoring it reopens old SET.
            assert await admin.execute_command("ACL", "SETUSER", name, "-set")
            for client in (old, modern):
                with pytest.raises(ResponseError, match="(?i)permission"):
                    await client.set(key, "denied", ex=300)
            assert await admin.execute_command("ACL", "SETUSER", name, "+set")
            assert await admin.set(key, "modern-new", ex=300) is True
            assert await old.set(key, "old-rollback", ex=300) is True
            assert await admin.get(key) == "old-rollback", (
                "S04_ACL_HARNESS_ERROR: unsafe rollback control not reproduced"
            )
        finally:
            if old is not None:
                await old.aclose()
            if modern is not None:
                await modern.aclose()
            await admin.delete(key)
            if created:
                await admin.execute_command("ACL", "DELUSER", name)
            await admin.aclose()
            await engine.dispose()
    asyncio.run(run())
