from typing import Dict, Type, Optional

from .algorithms.base import BaseRateLimiter
from .algorithms.token_bucket import TokenBucketLimiter
from .algorithms.sliding_window import SlidingWindowLimiter
from .storage.base import BaseStorage
from ..config.schemas import ConfigSchema


class RateLimiterFactory:
    """
    Tạo các instance của các thuật toán rate limiter khác nhau.
    """
    _limiter_classes: Dict[str, Type[BaseRateLimiter]] = {
        "token_bucket": TokenBucketLimiter,
        "sliding_window": SlidingWindowLimiter,
    }

    @classmethod
    def create_limiter(
        cls,
        algorithm: str,
        storage: BaseStorage,
        config: ConfigSchema
    ) -> Optional[BaseRateLimiter]:
        """Tạo một limiter instance dựa trên cấu hình trong settings."""
        limiter_class = cls._limiter_classes.get(algorithm)

        if not limiter_class:
            raise ValueError(f"Unknown rate limiting algorithm: {algorithm}")

        # Ủy quyền việc tạo instance cho chính class limiter đó
        return limiter_class.from_config(storage, config)