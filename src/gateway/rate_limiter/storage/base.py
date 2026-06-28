from abc import ABC, abstractmethod
from typing import Any, Tuple

class BaseStorage(ABC):
    """Interface cho các backend lưu trữ của Rate Limiter."""

    @abstractmethod
    async def consume_token_bucket(
        self, key: str, capacity: float, refill_rate: float, cost: float, ttl: int
    ) -> Tuple[bool, float, float]:
        """Thực thi thuật toán Token Bucket và trả về kết quả."""
        pass

    @abstractmethod
    async def consume_sliding_window(
        self, key: str, limit: int, window_size: int, ttl: int
    ) -> Tuple[bool, int]:
        """Thực thi thuật toán Sliding Window và trả về kết quả."""
        pass