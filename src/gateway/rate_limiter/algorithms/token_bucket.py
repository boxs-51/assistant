from typing import Tuple

from .base import BaseRateLimiter
from ..storage.base import BaseStorage


class TokenBucketLimiter(BaseRateLimiter):
    """Triển khai thuật toán Token Bucket."""

    def __init__(self, storage: BaseStorage, capacity: float, refill_rate: float, ttl: int):
        super().__init__(storage)
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.ttl = ttl

    async def is_allowed(self, key: str, cost: int = 1) -> Tuple[bool, int, float]:
        allowed, remaining, wait_time = await self.storage.consume_token_bucket(
            key=key, capacity=self.capacity, refill_rate=self.refill_rate, cost=float(cost), ttl=self.ttl
        )
        return allowed, int(remaining), wait_time