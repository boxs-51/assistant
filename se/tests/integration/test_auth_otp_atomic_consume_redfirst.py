"""Auth OTP T1 RED-first: atomic single-use consumption of cached OTP data.

Concurrent real InMemoryDriver reads and an adversarial count=0 delete.
No production source or cross-track cache API modification.
"""
import asyncio

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver
from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService


def test_two_concurrent_otp_consumers_cannot_both_receive_same_data():
    async def exercise():
        two_readers_ready = asyncio.Event()

        class BarrierReadCache(InMemoryDriver):
            def __init__(self):
                super().__init__(DriverConfig())
                self.readers = 0

            async def get(self, key):
                value = await super().get(key)
                if key == "pending_reg:race":
                    self.readers += 1
                    if self.readers == 2:
                        two_readers_ready.set()
                    await asyncio.wait_for(two_readers_ready.wait(), timeout=4)
                return value

        driver = BarrierReadCache()
        await driver.connect()
        payload = '{"purpose":"registration","otp":"123456","user_data":{"id":"u"}}'
        await driver.set("pending_reg:race", payload, expire=120)
        service = OTPStorageService(driver, uow_factory=lambda: None)
        consumed = await asyncio.gather(
            service._consume_cache_value("pending_reg:race"),
            service._consume_cache_value("pending_reg:race"),
        )
        assert driver.readers == 2, "Concurrency interleaving was not exercised"
        assert await driver.get("unrelated") is None
        assert sum(item == payload for item in consumed) == 1, (
            "RED-FIRST: two readers fetched the same OTP before one DEL "
            "completed; consuming the same OTP twice is not allowed"
        )
        await driver.disconnect()

    asyncio.run(exercise())


def test_zero_affected_delete_count_must_never_return_cached_otp():
    class ZeroCountCache(InMemoryDriver):
        async def delete(self, key):
            return 0  # Explicitly no physical deletion.

    async def exercise():
        driver = ZeroCountCache(DriverConfig())
        await driver.connect()
        await driver.set("pending_reg:zero", "opaque-payload", expire=120)
        otp = OTPStorageService(driver, uow_factory=lambda: None)
        consumed = await otp._consume_cache_value("pending_reg:zero")
        assert await driver.get("pending_reg:zero") == "opaque-payload"
        assert consumed is None, (
            "RED-FIRST: OTP cache consumer returned data despite atomic "
            "delete count 0; no one-use consumption was confirmed"
        )
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
