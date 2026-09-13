import pytest
from unittest.mock import AsyncMock

from src.transport.gateway.limiter import RateLimiterManager
from src.circuit_breaker import CircuitBreakerManager
from src.infrastructure.config.schemas import CircuitBreakerSettings, RateLimitSettings


class FakeCacheDriver:
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

    async def execute_script(self, script, keys, args):
        return [1, 99, 0]


@pytest.mark.asyncio
async def test_manager_accepts_cache_driver_abstraction():
    manager = RateLimiterManager(
        cache_driver=FakeCacheDriver(),
        circuit_breaker_manager=CircuitBreakerManager(CircuitBreakerSettings()),
        config=RateLimitSettings()
    )

    assert manager.limiter is not None