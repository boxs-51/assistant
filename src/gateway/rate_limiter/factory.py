from typing import Dict, Type, Optional

from .algorithms.base import BaseRateLimiter
from .algorithms.token_bucket import TokenBucketLimiter
from .algorithms.sliding_window import SlidingWindowLimiter
from .storage.base import BaseStorage
from ..config import settings


class RateLimiterFactory:
    """
    Tạo các instance của các thuật toán rate limiter khác nhau.
    """
    _limiter_classes: Dict[str, Type[BaseRateLimiter]] = {
        "token_bucket": TokenBucketLimiter,
        "sliding_window": SlidingWindowLimiter,
    }

    @classmethod
    def create_limiter(cls, storage: BaseStorage) -> Optional[BaseRateLimiter]:
        """Tạo một limiter instance dựa trên cấu hình trong settings."""
        algorithm = settings.rate_limit.algorithm.lower()
        limiter_class = cls._limiter_classes.get(algorithm)

        if not limiter_class:
            raise ValueError(f"Unknown rate limiting algorithm: {algorithm}")

        if algorithm == "token_bucket":
            return TokenBucketLimiter(storage, settings.rate_limit.capacity, settings.rate_limit.refill_rate, settings.redis.cache_expire_seconds)
        elif algorithm == "sliding_window":
            return SlidingWindowLimiter(storage, settings.rate_limit.limit, settings.rate_limit.window_size, settings.redis.cache_expire_seconds)
        
        return None