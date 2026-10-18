"""R-ACK-08: additional real Redis/PG17 cross-instance OTP issuance witness.

The existing R-ACK-07 assertion is deliberately untouched. This parametrized
regression extends the exact failed scenario to password resets. No mock Redis,
no fake PostgreSQL COMMIT, and no external/production endpoints.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import redis.asyncio as redis

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.redis.driver import RedisDriver
from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService
from se.tests.integration.fixtures.ae359_redis_resp_ack_cut import RespAckCutProxy
from se.tests.integration.test_ae359_post_ack_loss_physical import (
    _targets, _pg_engines, _redis_driver, _factory, _row_count, _finish,
)
from se.src.infrastructure.storage.models.sql.user_data.pending_registration import PendingRegistration
from se.src.infrastructure.storage.repositories.user_data.pending_registrations import (
    OTPIssuanceConflict,
)


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_r_ack_08_cross_instance_lost_ack_never_issues_second_sql_challenge(purpose):
    async def run():
        ru, _ = _targets()
        engine = await _pg_engines()
        email = f"rack08-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown = ("password_reset_cooldown:" if reset else "otp_cooldown:") + email
        direct = redis.Redis.from_url(ru, decode_responses=True)
        save_name = "save_pending_password_reset" if reset else "save_pending_registration"
        verify_name = "verify_password_reset" if reset else "verify_and_get_data"
        try:
            assert await direct.ping(), "physical Redis required"
            async with RespAckCutProxy() as proxy:
                writer = await _redis_driver(f"redis://127.0.0.1:{proxy.port}/15")
                service = OTPStorageService(writer, _factory(engine))
                proxy.arm_once()
                first_task = asyncio.create_task(
                    getattr(service, save_name)(email, {"value": "redis-first"}, "111111")
                )
                await proxy.wait_captured()
                assert proxy.client_ack_bytes == 0
                assert await direct.exists(key) == 1, "real Redis SET did not execute"
                proxy.release_drop.set()
                status, detail = await _finish(first_task)
                assert status == "ERROR", f"unknown ACK must fail closed: {status} {detail}"
                await writer.disconnect()

            cold = RedisDriver(DriverConfig(options={"url": ru}))
            assert cold._client is None, "new process/instance must be cold"
            second = OTPStorageService(cold, _factory(engine))
            try:
                await getattr(second, save_name)(email, {"value": "sql-second"}, "222222")
            except Exception:
                # A durable fencing rejection is a valid fail-closed outcome.
                pass

            reader = await _redis_driver(ru)
            try:
                verifier = OTPStorageService(reader, _factory(engine))
                first = await getattr(verifier, verify_name)(email, "111111")
                second_result = await getattr(verifier, verify_name)(email, "222222")
                assert first == {"value": "redis-first"}, "first live challenge must survive"
                assert second_result is None, (
                    "SEMANTIC_RED: second SQL challenge independently authenticated "
                    f"after lost ACK ({purpose})"
                )
                assert await _row_count(engine, email) == 0, (
                    "SEMANTIC_RED: SQL fallback persisted another live challenge"
                )
            finally:
                await reader.disconnect()
        finally:
            await direct.delete(key, cooldown)
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await direct.aclose()
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("purpose", ("registration", "password_reset"))
def test_r_ack_09_competing_pg17_writers_issue_only_one_redis_challenge(purpose):
    """Actual concurrent PG17 issuance reservations serialize before Redis SET."""
    async def run():
        ru, _ = _targets()
        engine = await _pg_engines()
        email = f"rack09-{uuid4().hex}@example.invalid"
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown = ("password_reset_cooldown:" if reset else "otp_cooldown:") + email
        first_driver = await _redis_driver(ru)
        second_driver = await _redis_driver(ru)
        direct = redis.Redis.from_url(ru, decode_responses=True)
        save_name = "save_pending_password_reset" if reset else "save_pending_registration"
        verify_name = "verify_password_reset" if reset else "verify_and_get_data"
        try:
            writers = [
                OTPStorageService(first_driver, _factory(engine)),
                OTPStorageService(second_driver, _factory(engine)),
            ]
            results = await asyncio.gather(
                getattr(writers[0], save_name)(
                    email, {"value": "first"}, "111111"
                ),
                getattr(writers[1], save_name)(
                    email, {"value": "second"}, "222222"
                ),
                return_exceptions=True,
            )
            assert sum(isinstance(r, OTPIssuanceConflict) for r in results) == 1, (
                "SEMANTIC_RED: second worker did not lose cross-instance fence",
                [type(r).__name__ for r in results],
            )
            assert sum(r is None for r in results) == 1
            verifier = OTPStorageService(first_driver, _factory(engine))
            first = await getattr(verifier, verify_name)(email, "111111")
            second = await getattr(verifier, verify_name)(email, "222222")
            assert (first is None) != (second is None), (
                "SEMANTIC_RED: both OTPs authenticated or both were lost"
            )
            assert await _row_count(engine, email) == 0
        finally:
            await first_driver.disconnect()
            await second_driver.disconnect()
            await direct.delete(key, cooldown)
            async with engine.begin() as conn:
                await conn.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await direct.aclose()
            await engine.dispose()

    asyncio.run(run())
