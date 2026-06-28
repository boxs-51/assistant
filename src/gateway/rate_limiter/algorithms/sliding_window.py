from typing import Tuple

from .base import BaseRateLimiter
from ..storage.base import BaseStorage


class SlidingWindowLimiter(BaseRateLimiter):
    """Triển khai thuật toán Sliding Window."""

    def __init__(self, storage: BaseStorage, limit: int, window_size: int, ttl: int):
        super().__init__(storage)
        self.limit = limit
        self.window_size = window_size
        self.ttl = ttl

    async def is_allowed(self, key: str, cost: int = 1) -> Tuple[bool, int, float]:
        # Thuật toán Sliding Window đơn giản không tính đến 'cost', mỗi request là 1.
        allowed, remaining = await self.storage.consume_sliding_window(
            key=key, limit=self.limit, window_size=self.window_size, ttl=self.ttl
        )
        # Sliding window không có khái niệm "thời gian chờ", hoặc là được hoặc không.
        return allowed, remaining, 0.0