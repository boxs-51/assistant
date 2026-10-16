"""Portable two-OS security oracles for AE359 OTP write uncertainty.

No external services, secrets or production credentials. Post-dispatch
exceptions must never become SQL fallback. Physical Redis/PG17 proofs live in
test_ae359_post_ack_loss_physical.py and PG17 atomic test suites.
"""
import asyncio

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError, TimeoutError as RedisTimeoutError

import se.src.transport.gateway.authentication.services.otp_service as otp_module
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.redis.driver import RedisDriver
from se.src.infrastructure.storage.interfaces.cache import CacheWriteNotDispatched
from se.src.transport.gateway.authentication.services.otp_service import OTPStorageService


MODES = ("registration", "password_reset")
UNCERTAIN = (RedisConnectionError, RedisTimeoutError, ResponseError,
             ConnectionResetError, BrokenPipeError, RuntimeError)


def _call(service, purpose):
    return (service.save_pending_registration if purpose == "registration"
            else service.save_pending_password_reset)(
                "otp@example.invalid", {"synthetic": True}, "123456"
            )


class _Uow:
    def __init__(self, *, failure=None):
        self.pending_registrations = self
        self.rows = []
        self.commits = 0
        self.failure = failure

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def create_or_update(self, **kwargs):
        self.rows.append(kwargs)

    async def commit(self):
        self.commits += 1
        if self.failure:
            raise self.failure

    async def get_remaining_cooldown(self, *_):
        return 0


class _Cache:
    def __init__(self, *, fail_step=None, exc=None):
        self.calls = []
        self.fail_step = fail_step
        self.exc = exc

    async def set(self, key, value, expire):
        self.calls.append(key)
        if len(self.calls) == self.fail_step:
            raise self.exc

    async def get_ttl(self, *_):
        raise RuntimeError("SENSITIVE_SENTINEL_NEVER_LOG")

    async def consume_one_time(self, *_):
        raise RuntimeError("SENSITIVE_SENTINEL_NEVER_LOG")


class _Logger:
    def __init__(self):
        self.events = []

    def error(self, message, **kw):
        self.events.append((message, kw))

    def warning(self, message, **kw):
        self.events.append((message, kw))


@pytest.mark.parametrize("purpose", MODES)
@pytest.mark.parametrize("exc_type", UNCERTAIN)
def test_postdispatch_uncertain_write_never_falls_back(purpose, exc_type):
    uow = _Uow()
    exc = exc_type("SENSITIVE_SENTINEL_NEVER_LOG")
    cache = _Cache(fail_step=1, exc=exc)
    service = OTPStorageService(cache, lambda: uow)
    with pytest.raises(exc_type):
        asyncio.run(_call(service, purpose))
    assert len(cache.calls) == 1
    assert uow.rows == []
    assert uow.commits == 0


@pytest.mark.parametrize("purpose", MODES)
@pytest.mark.parametrize("exc_type", (RedisConnectionError, RedisTimeoutError,
                                      CacheWriteNotDispatched, RuntimeError))
def test_cooldown_failure_even_predispatch_never_falls_back(purpose, exc_type):
    uow = _Uow()
    cache = _Cache(fail_step=2, exc=exc_type("cooldown"))
    service = OTPStorageService(cache, lambda: uow)
    with pytest.raises(exc_type):
        asyncio.run(_call(service, purpose))
    assert len(cache.calls) == 2
    assert uow.rows == []
    assert uow.commits == 0


@pytest.mark.parametrize("purpose", MODES)
@pytest.mark.parametrize("mode", ("no_cache_configured", "attested_predispatch"))
def test_only_absent_cache_or_typed_predispatch_may_fallback(purpose, mode):
    uow = _Uow()
    cache = (None if mode == "no_cache_configured" else
             _Cache(fail_step=1, exc=CacheWriteNotDispatched("not sent")))
    service = OTPStorageService(cache, lambda: uow)
    asyncio.run(_call(service, purpose))
    assert len(uow.rows) == 1
    assert uow.commits == 1
    assert cache is None or len(cache.calls) == 1


def test_redis_driver_uninitialized_is_attested_before_dispatch():
    driver = RedisDriver(DriverConfig(options={"url": "redis://127.0.0.1:6379/15"}))
    with pytest.raises(CacheWriteNotDispatched):
        asyncio.run(driver.set("otp", "synthetic", 60))


@pytest.mark.parametrize("reply", (True, False, None, RedisTimeoutError("ACK lost")))
def test_redis_driver_requires_positive_receipt_not_merely_no_exception(reply):
    class Client:
        async def set(self, *args, **kwargs):
            if isinstance(reply, Exception):
                raise reply
            return reply

    driver = RedisDriver(DriverConfig(options={"url": "redis://127.0.0.1:6379/15"}))
    driver._client = Client()
    if reply is True:
        asyncio.run(driver.set("otp", "synthetic", 60))
    else:
        with pytest.raises((RuntimeError, RedisTimeoutError)):
            asyncio.run(driver.set("otp", "synthetic", 60))


@pytest.mark.parametrize("method", ("check_cooldown", "check_password_reset_cooldown"))
def test_cooldown_lookup_never_logs_redis_exception_message(monkeypatch, method):
    spy = _Logger()
    monkeypatch.setattr(otp_module, "logger", spy)
    uow = _Uow()
    service = OTPStorageService(_Cache(), lambda: uow)
    assert asyncio.run(getattr(service, method)("otp@example.invalid")) == 0
    assert spy.events
    assert "SENSITIVE_SENTINEL_NEVER_LOG" not in str(spy.events)
    assert spy.events[0][1]["error_type"] == "RuntimeError"


def test_consume_uncertain_receipt_does_not_log_credentials_or_fall_back(monkeypatch):
    spy = _Logger()
    monkeypatch.setattr(otp_module, "logger", spy)
    uow = _Uow()
    service = OTPStorageService(_Cache(), lambda: uow)
    with pytest.raises(RuntimeError):
        asyncio.run(service.verify_and_get_data("otp@example.invalid", "123456"))
    assert uow.commits == 0 and uow.rows == []
    assert len(spy.events) == 1
    assert "SENSITIVE_SENTINEL_NEVER_LOG" not in str(spy.events)
    assert spy.events[0][1]["error_type"] == "RuntimeError"
    assert spy.events[0][1].get("exc_info") is not True


@pytest.mark.parametrize("purpose", MODES)
def test_db_only_commit_failure_not_acknowledged(purpose):
    uow = _Uow(failure=RuntimeError("synthetic SQL rollback"))
    service = OTPStorageService(None, lambda: uow)
    with pytest.raises(RuntimeError, match="synthetic SQL rollback"):
        asyncio.run(_call(service, purpose))
    assert len(uow.rows) == 1 and uow.commits == 1
