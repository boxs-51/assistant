import os
from typing import Tuple
from redis.commands.core import Script
import redis.asyncio as redis
import structlog

from .base import BaseStorage

logger = structlog.get_logger(__name__)

class RedisStorage(BaseStorage):
    """Triển khai Storage sử dụng Redis và Lua script để đảm bảo tính nguyên tử."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.token_bucket_script = self._load_lua_script('token_bucket.lua')
        self.sliding_window_script = self._load_lua_script('sliding_window.lua')

    def _load_lua_script(self, filename: str) -> "redis.client.Script":
        """Tải một Lua script từ file và đăng ký nó với Redis."""
        script_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', filename)
        with open(script_path, 'r', encoding="utf-8") as f:
            script_code = f.read()
        return self.redis.register_script(script_code)

    async def consume_token_bucket(
        self, key: str, capacity: float, refill_rate: float, cost: float, ttl: int
    ) -> Tuple[bool, float, float]:
        """
        Gọi Lua script để thực thi thuật toán Token Bucket một cách nguyên tử.
        """
        import time
        current_time = time.time()
        
        result = await self.token_bucket_script(keys=[key], args=[capacity, refill_rate, cost, current_time, ttl])
        
        allowed, remaining_tokens, wait_time = result
        return bool(allowed), float(remaining_tokens), float(wait_time)

    async def consume_sliding_window(
        self, key: str, limit: int, window_size: int, ttl: int
    ) -> Tuple[bool, int]:
        """
        Gọi Lua script để thực thi thuật toán Sliding Window một cách nguyên tử.
        """
        import time
        current_time = time.time()

        result = await self.sliding_window_script(keys=[key], args=[limit, window_size, current_time, ttl])
        allowed, remaining = result
        return bool(allowed), int(remaining)