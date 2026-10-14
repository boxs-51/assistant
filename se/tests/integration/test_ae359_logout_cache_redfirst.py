"""#359/#522 RED-first logout ACK witnesses (EVIDENCE ONLY, intentionally failing).

Exercise the real FastAPI logout route -> Authentication.logout ->
TokenService.logout -> SessionRepository.delete_token, including a genuine
redis-py TCP connection failure (no mocked exception from the auth facade).

DO NOT change assertions to make an unsafe 204 response green.
This is NOT a production session-generation or physical dispatch-fence test.
"""
from __future__ import annotations

import asyncio
import hashlib
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.inmemory.driver import InMemoryDriver
from se.src.infrastructure.storage.drivers.redis.driver import RedisDriver
from se.src.infrastructure.storage.repositories.sessions import SessionRepository
from se.src.transport.gateway.api.v1 import auth_router
from se.src.transport.gateway.authentication.authentication import Authentication
from se.src.transport.gateway.authentication.services.token_service import TokenService


REFRESH = "red-first-diagnostic-refresh-token-not-a-credential"
USER_ID = "red-first-user"


def _key() -> str:
    return "session:refresh_token:" + hashlib.sha256(REFRESH.encode()).hexdigest()


def _client_with_real_service_chain(driver) -> TestClient:
    # Leave real logout implementation intact. Bypass ONLY unrelated login
    # dependencies and configure the existing service/repository seam.
    token_service = TokenService.__new__(TokenService)
    token_service.session_repo = SessionRepository(driver)
    facade = Authentication.__new__(Authentication)
    facade.token_service = token_service

    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[auth_router.get_auth] = lambda: facade
    app.dependency_overrides[auth_router.get_event_bus] = lambda: None
    return TestClient(app)


def _logout(client: TestClient):
    return client.post("/v1/auth/logout", json={"refresh_token": REFRESH})


def test_real_redis_driver_disconnected_runtime_error_must_not_ack_204():
    # RedisDriver._require_client raises RuntimeError; repository currently
    # swallows it. No facade mock injects the exception.
    driver = RedisDriver(DriverConfig(options={"url": "redis://127.0.0.1:1/0"}))
    assert driver.is_connected is False
    with _client_with_real_service_chain(driver) as client:
        response = _logout(client)
    assert response.status_code == 503, (
        "RED-FIRST: RedisDriver disconnected RuntimeError was swallowed, "
        f"logout returned {response.status_code} instead of 503"
    )


def test_real_redis_py_tcp_backend_failure_must_not_ack_204():
    # A socket is deliberately BOUND but NOT LISTENING on loopback, ensuring
    # connection refusal on both native Linux and Windows without a Redis
    # daemon or synthetic RedisError/monkeypatch.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        driver = RedisDriver(
            DriverConfig(options={"url": f"redis://127.0.0.1:{port}/0"})
        )
        from redis import asyncio as redis_asyncio
        driver._client = redis_asyncio.from_url(
            driver.url,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
            retry_on_timeout=False,
            decode_responses=True,
        )
        driver._connected = True
        try:
            with _client_with_real_service_chain(driver) as client:
                response = _logout(client)
        finally:
            asyncio.run(driver.disconnect())
    assert response.status_code == 503, (
        "RED-FIRST: genuine redis-py socket failure was swallowed, "
        f"logout returned {response.status_code} instead of 503"
    )


def test_absent_refresh_hash_cannot_receive_confirmed_revoke_ack():
    # Product contract proposal: do not claim revocation confirmed when the
    # caller provides an absent, unauthenticated refresh hash.
    driver = InMemoryDriver(DriverConfig())
    asyncio.run(driver.connect())
    assert asyncio.run(driver.get(_key())) is None
    with _client_with_real_service_chain(driver) as client:
        response = _logout(client)
    assert response.status_code != 204, (
        "RED-FIRST: missing refresh hash got success ACK with no proof "
        "of a previously authenticated revocation"
    )


class _NoOpDeleteCache(InMemoryDriver):
    """Instrumented backend proving successful return != physical deletion."""

    def __init__(self):
        super().__init__(DriverConfig())
        self.delete_calls = []

    async def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        # Deliberately do nothing, exactly as the current void cache contract
        # permits. This is an adversarial no-op, NOT a real Redis failure.


def test_backend_noop_delete_must_not_ack_revocation():
    driver = _NoOpDeleteCache()
    asyncio.run(driver.connect())
    asyncio.run(driver.set(_key(), USER_ID, expire=3600))
    with _client_with_real_service_chain(driver) as client:
        response = _logout(client)
    assert driver.delete_calls == [_key()]
    assert asyncio.run(driver.get(_key())) == USER_ID, (
        "No-op witness invalid: token must still exist"
    )
    assert response.status_code != 204, (
        "RED-FIRST: backend returned without deleting token but API ACKed 204"
    )


def test_successful_existing_token_deletion_retains_scoped_204():
    # Positive control: the existing, physically deleted refresh hash
    # retains the narrowly scoped successful response.
    driver = InMemoryDriver(DriverConfig())
    asyncio.run(driver.connect())
    asyncio.run(driver.set(_key(), USER_ID, expire=3600))
    assert asyncio.run(driver.get(_key())) == USER_ID
    with _client_with_real_service_chain(driver) as client:
        response = _logout(client)
    assert response.status_code == 204
    assert asyncio.run(driver.get(_key())) is None


# P0B positive and adversarial contract checks. The original five RED-first
# witness tests above are unchanged from frozen PR535.
def test_missing_refresh_hash_returns_401_with_no_revocation_ack():
    driver = InMemoryDriver(DriverConfig())
    asyncio.run(driver.connect())
    with _client_with_real_service_chain(driver) as client:
        response = _logout(client)
    assert response.status_code == 401
    assert REFRESH not in response.text


def test_backend_void_noop_delete_returns_503_not_204():
    driver = _NoOpDeleteCache()
    asyncio.run(driver.connect())
    asyncio.run(driver.set(_key(), USER_ID, expire=3600))
    with _client_with_real_service_chain(driver) as client:
        response = _logout(client)
    assert response.status_code == 503
    assert asyncio.run(driver.get(_key())) == USER_ID
    assert REFRESH not in response.text


@pytest.mark.parametrize("unconfirmed", [None, True, False, -1, 2])
def test_nonconforming_backend_delete_receipt_fails_closed(unconfirmed):
    class _MalformedReceiptCache(InMemoryDriver):
        async def delete(self, key):
            return unconfirmed

    driver = _MalformedReceiptCache(DriverConfig())
    asyncio.run(driver.connect())
    asyncio.run(driver.set(_key(), USER_ID, expire=3600))
    with _client_with_real_service_chain(driver) as client:
        response = _logout(client)
    assert response.status_code == 503
    assert asyncio.run(driver.get(_key())) == USER_ID


def test_inmemory_atomic_delete_count_missing_expired_and_existing():
    import time

    async def check():
        driver = InMemoryDriver(DriverConfig())
        await driver.connect()
        assert await driver.delete("missing") == 0
        await driver.set("live", "user", expire=3600)
        assert await driver.delete("live") == 1
        assert await driver.delete("live") == 0
        await driver.set("expired", "user", expire=3600)
        # Simulate an expired TTL without slowing or relying on wall time.
        driver._expires["expired"] = time.monotonic() - 1
        assert await driver.delete("expired") == 0
        assert await driver.get("expired") is None

    asyncio.run(check())


def test_inmemory_two_concurrent_deletes_exactly_one_receipt():
    async def check():
        driver = InMemoryDriver(DriverConfig())
        await driver.connect()
        await driver.set(_key(), USER_ID, expire=3600)
        repo = SessionRepository(driver)
        receipts = await asyncio.gather(
            repo.delete_token(hashlib.sha256(REFRESH.encode()).hexdigest()),
            repo.delete_token(hashlib.sha256(REFRESH.encode()).hexdigest()),
        )
        assert sorted(receipts) == [0, 1]
        assert await driver.get(_key()) is None

    asyncio.run(check())


def test_redis_driver_propagates_atomic_del_receipt():
    # RedisDriver contract unit check; actual redis-py socket failure is
    # independently covered by the unchanged RED-first test above.
    from unittest.mock import AsyncMock

    async def check():
        driver = RedisDriver(DriverConfig())
        fake_client = type("_RedisDelClient", (), {})()
        fake_client.delete = AsyncMock(side_effect=[1, 0, True])
        driver._client = fake_client
        assert await driver.delete("cached-key") == 1
        assert await driver.delete("cached-key") == 0
        with pytest.raises(RuntimeError, match="unconfirmed deletion count"):
            await driver.delete("cached-key")
        assert fake_client.delete.await_count == 3

    asyncio.run(check())


def test_otp_cache_consumer_remains_compatible_with_delete_receipt():
    # CacheDriver.delete now reports an int; OTPStorageService ignores the
    # successful receipt, preserving the existing OTP consume call signature.
    from se.src.transport.gateway.authentication.services.otp_service import (
        OTPStorageService,
    )

    async def check():
        driver = InMemoryDriver(DriverConfig())
        await driver.connect()
        await driver.set("pending_reg:contract", "opaque-otp", expire=60)
        otp = OTPStorageService(driver, uow_factory=lambda: None)
        assert await otp._consume_cache_value("pending_reg:contract") == "opaque-otp"
        assert await otp._consume_cache_value("pending_reg:contract") is None

    asyncio.run(check())
