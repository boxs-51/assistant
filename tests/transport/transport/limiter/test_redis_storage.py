import pytest
from unittest.mock import AsyncMock

from src.transport.gateway.limiter.storage.redis_storage import RedisStorage


class FakeCacheDriver:
    def __init__(self):
        self.execute_script = AsyncMock(
            return_value=[1, 99, 0],
        )

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def ping(self):
        return True

    async def get(self, key):
        return None

    async def set(self, key, value, expire=None):
        pass

    async def delete(self, key):
        pass


@pytest.mark.asyncio
async def test_constructor_does_not_execute_redis_io():
    cache = FakeCacheDriver()

    storage = RedisStorage(cache)

    assert "token_bucket" in storage.scripts
    assert "sliding_window" in storage.scripts
    cache.execute_script.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_delegates_to_cache_driver():
    cache = FakeCacheDriver()
    storage = RedisStorage(cache)

    result = await storage.execute(
        "token_bucket",
        ["rate:key"],
        [100, 5, 1, 3600],
    )

    assert result == [1, 99, 0]
    cache.execute_script.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_script_raises():
    cache = FakeCacheDriver()
    storage = RedisStorage(cache)

    with pytest.raises(
        ValueError,
        match="Script 'unknown' not found",
    ):
        await storage.execute(
            "unknown",
            ["key"],
            [],
        )