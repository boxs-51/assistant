"""AE359: RedisDriver.connect() must never log credential-bearing material.

Synthetic fixtures only. No Redis service, external connection, or real secret.
The test inspects structured logger call arguments, including failure cleanup.
"""
import asyncio

import pytest

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.redis import driver as redis_module


@pytest.mark.parametrize(
    ("ping_fails", "close_fails"),
    [(False, False), (True, False), (True, True)],
)
def test_redis_connect_logs_never_contain_secrets(
    monkeypatch, ping_fails, close_fails
):
    secret = "AE359_SYNTHETIC_REDIS_PASSWORD_NEVER_LOG"
    url = f"redis://:{secret}@127.0.0.1:6379/0"
    log_calls = []
    redis_urls = []

    class Recorder:
        def info(self, event, **kwargs):
            log_calls.append(("info", event, kwargs))

        def error(self, event, **kwargs):
            log_calls.append(("error", event, kwargs))

        def debug(self, event, **kwargs):
            log_calls.append(("debug", event, kwargs))

    class FakeRedisClient:
        def __init__(self):
            self.closed = False

        async def ping(self):
            if ping_fails:
                raise OSError(f"synthetic ping failed; url={url}")
            return True

        async def aclose(self):
            self.closed = True
            if close_fails:
                raise OSError(f"synthetic cleanup failed; password={secret}")

    client = FakeRedisClient()

    def fake_from_url(configured_url, *, decode_responses):
        redis_urls.append((configured_url, decode_responses))
        return client

    monkeypatch.setattr(redis_module, "logger", Recorder())
    monkeypatch.setattr(redis_module.redis, "from_url", fake_from_url)

    async def exercise():
        driver = redis_module.RedisDriver(DriverConfig(options={"url": url}))
        if ping_fails:
            with pytest.raises(OSError, match="synthetic ping failed"):
                await driver.connect()
            assert driver._client is None
            assert driver.is_connected is False
            assert client.closed is True
        else:
            await driver.connect()
            assert driver.is_connected is True
            await driver.disconnect()
            assert client.closed is True

    asyncio.run(exercise())

    # Credentials are still transmitted to redis.from_url, never discarded.
    assert redis_urls == [(url, True)]
    assert log_calls, "Test must inspect real connect() logger invocations"
    for level, event, fields in log_calls:
        emitted = repr((level, event, fields))
        assert secret not in emitted, "connect() disclosed a Redis secret to logs"
        assert url not in emitted, "connect() disclosed a Redis connection URL"
        assert fields.get("exc_info") is not True, (
            "Logging exception tracebacks could disclose connection credentials"
        )
