"""Child-only AE359 OTP T1 semantic oracles for atomic value-returning consume.

The original GET-barrier and DEL=0 RED witnesses remain frozen in PR545.
This file is a successor test-only revision: no production GET is ever
required to satisfy an oracle and no cache driver/source is modified.
Correct consumers must atomically pop the stored value, or fail closed on
an ambiguous/unconfirmed backend receipt. The current GET->DELETE source
must remain physically RED against these tests.
"""

import asyncio

import pytest

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver
from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService


def test_two_concurrent_otp_consumers_cannot_both_receive_same_data():
    async def exercise():
        key = "pending_reg:race"
        payload = '{"purpose":"registration","otp":"123456","user_data":{"id":"u"}}'

        class YieldingLegacyReadCache(InMemoryDriver):
            async def get(self, requested_key):
                value = await super().get(requested_key)
                if requested_key == key and value is not None:
                    # Expose a reachable stale-GET race in the OLD implementation.
                    # The correct atomic-pop implementation never calls get().
                    # There is no GET barrier, reader-count condition or
                    # requirement to perform an unsafe read.
                    await asyncio.sleep(0)
                return value

        driver = YieldingLegacyReadCache(DriverConfig())
        await driver.connect()
        try:
            await driver.set(key, payload, expire=120)
            service = OTPStorageService(driver, uow_factory=lambda: None)
            start = asyncio.Event()

            async def consume_when_released():
                await start.wait()
                return await service._consume_cache_value(key)

            tasks = [asyncio.create_task(consume_when_released()) for _ in range(2)]
            start.set()
            consumed = await asyncio.gather(*tasks)

            assert sum(value == payload for value in consumed) == 1, (
                "RED-FIRST: two consumers must never receive one OTP payload"
            )
            assert sum(value is None for value in consumed) == 1, (
                "Atomic consumption must confirm a single winner and one miss"
            )
            assert await driver.get(key) is None, "Consumed OTP must be absent"
        finally:
            await driver.disconnect()

    asyncio.run(exercise())


def test_unconfirmed_atomic_pop_receipt_must_fail_closed_without_deletion():
    class UnconfirmedPopCache(InMemoryDriver):
        async def consume_one_time(self, key):
            # A boolean is not an OTP payload nor an atomic pop confirmation.
            # The backend deliberately retains the original OTP.
            return True

    async def exercise():
        key = "pending_reg:unconfirmed"
        payload = '{"purpose":"registration","otp":"123456","user_data":{"id":"u"}}'
        driver = UnconfirmedPopCache(DriverConfig())
        await driver.connect()
        try:
            await driver.set(key, payload, expire=120)
            service = OTPStorageService(driver, uow_factory=lambda: None)
            with pytest.raises(RuntimeError, match="(?i)(unconfirmed|invalid|receipt|atomic|consume)"):
                await service._consume_cache_value(key)
            assert await driver.get(key) == payload, (
                "Unconfirmed atomic pop must not be treated as a consumed OTP"
            )
        finally:
            await driver.disconnect()

    asyncio.run(exercise())


def test_uncertain_atomic_pop_must_not_fallback_to_database():
    class UncertainPopCache(InMemoryDriver):
        async def consume_one_time(self, key):
            # Simulates an ambiguous/failed backend receipt, not proven MISSING.
            raise RuntimeError("atomic pop ambiguous")

    def forbidden_db_fallback():
        pytest.fail("UNCERTAIN atomic pop cannot fall back to a stale DB OTP")

    async def exercise():
        key = "pending_reg:ambiguous@example.invalid"
        payload = '{"purpose":"registration","otp":"123456","user_data":{"id":"u"}}'
        driver = UncertainPopCache(DriverConfig())
        await driver.connect()
        try:
            await driver.set(key, payload, expire=120)
            service = OTPStorageService(driver, uow_factory=forbidden_db_fallback)
            with pytest.raises(RuntimeError, match="atomic pop ambiguous"):
                await service.verify_and_get_data("ambiguous@example.invalid", "123456")
            assert await driver.get(key) == payload, (
                "The simulated pre-consume failure left its OTP untouched"
            )
        finally:
            await driver.disconnect()

    asyncio.run(exercise())


def test_positive_control_sequential_consume_returns_payload_only_once():
    async def exercise():
        driver = InMemoryDriver(DriverConfig())
        await driver.connect()
        await driver.set("pending_reg:control", "opaque-payload", expire=120)
        otp = OTPStorageService(driver, uow_factory=lambda: None)
        assert await otp._consume_cache_value("pending_reg:control") == "opaque-payload"
        assert await otp._consume_cache_value("pending_reg:control") is None
        await driver.disconnect()

    asyncio.run(exercise())


def test_atomic_otp_consume_must_never_call_separate_get():
    class NoLegacyGet(InMemoryDriver):
        async def get(self, key):
            raise AssertionError("Unsafe separate GET on OTP consume path")

    async def exercise():
        driver = NoLegacyGet(DriverConfig())
        await driver.connect()
        try:
            key = "pending_reg:no-legacy-get"
            await driver.set(key, "single-use-payload", expire=120)
            service = OTPStorageService(driver, uow_factory=lambda: None)
            assert await service._consume_cache_value(key) == "single-use-payload"
            assert await service._consume_cache_value(key) is None
        finally:
            await driver.disconnect()

    asyncio.run(exercise())


def test_atomic_otp_pop_respects_ttl_and_does_not_resurrect_expired_value():
    async def exercise():
        driver = InMemoryDriver(DriverConfig())
        await driver.connect()
        try:
            key = "pending_reg:expired"
            await driver.set(key, "stale-payload", expire=0)
            service = OTPStorageService(driver, uow_factory=lambda: None)
            assert await service._consume_cache_value(key) is None
            assert await driver.consume_one_time(key) is None
        finally:
            await driver.disconnect()

    asyncio.run(exercise())
