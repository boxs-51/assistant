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
        reset = purpose == "password_reset"
        key = ("pending_password_reset:" if reset else "pending_reg:") + email
        cooldown_key = (
            "password_reset_cooldown:" if reset else "otp_cooldown:"
        ) + email
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
            try:
                await getattr(modern, save_name)(
                    email, {"value": "modern-attempt"}, "222222"
                )
            except Exception:
                # Denial is allowed, provided the original is still usable.
                outcome = "REJECTED"
            else:
                outcome = "SUCCESS"

            actual = await old_client.get(key)
            assert actual == original, (
                "S04_SEMANTIC_RED: modern SET destroyed an active pre-fence "
                f"legacy OTP ({purpose}; outcome={outcome})"
            )
            assert outcome == "REJECTED", (
                "S04_SEMANTIC_RED: modern writer acknowledged another issuance "
                f"despite active legacy challenge ({purpose})"
            )
            assert await _row_count(engine, email) == 0, (
                "S04_SEMANTIC_RED: a second SQL OTP authority was persisted"
            )

            verifier = OTPStorageService(new_driver, _factory(engine))
            first = await getattr(verifier, verify_name)(email, "111111")
            second = await getattr(verifier, verify_name)(email, "222222")
            replay = await getattr(verifier, verify_name)(email, "111111")
            assert first == {"value": "original-legacy"}, "legacy OTP lost"
            assert second is None, "S04_SEMANTIC_RED: second OTP authenticated"
            assert replay is None, "S04_SEMANTIC_RED: first OTP replayed"
        finally:
            if new_driver is not None:
                await new_driver.disconnect()
            await old_client.delete(key, cooldown_key)
            async with engine.begin() as connection:
                await connection.execute(
                    PendingRegistration.__table__.delete().where(
                        PendingRegistration.email == email
                    )
                )
            await old_client.aclose()
            await engine.dispose()

    asyncio.run(run())
