from typing import Tuple, Type
import structlog

from .base import BaseRateLimiter
from ..storage.base import BaseStorage
from ....config.schemas import ConfigSchema

logger = structlog.get_logger(__name__)

class SlidingWindowLimiter(BaseRateLimiter):
    """Triển khai thuật toán Sliding Window."""

    def __init__(self, storage: BaseStorage, limit: int, window_size: int, ttl: int):
        super().__init__(storage)
        self.limit = limit
        self.window_size = window_size
        self.ttl = ttl

        # Config Validation: Cảnh báo nếu TTL nhỏ hơn kích thước cửa sổ
        if self.ttl < self.window_size:
            logger.warning(
                "TTL is less than window_size for Sliding Window, which can lead to incorrect rate limiting behavior. The key might expire before the window is complete.",
                ttl=self.ttl,
                window_size=self.window_size
            )

    @classmethod
    def from_config(cls: Type["SlidingWindowLimiter"], storage: BaseStorage, config: ConfigSchema) -> "SlidingWindowLimiter":
        """Tạo SlidingWindowLimiter từ cấu hình ứng dụng."""
        rate_limit_settings = config.rate_limit
        redis_settings = config.redis

        logger.info(
            "Initializing SlidingWindowLimiter",
            limit=rate_limit_settings.limit,
            window_size=rate_limit_settings.window_size,
            ttl=redis_settings.cache_expire_seconds
        )
        return cls(storage, rate_limit_settings.limit, rate_limit_settings.window_size, redis_settings.cache_expire_seconds)

    async def is_allowed(self, key: str, cost: int = 1) -> Tuple[bool, int, float]:
        # Thuật toán Sliding Window đơn giản không tính đến 'cost', mỗi request là 1.
        # Tương tự TokenBucket, nó gọi executor chung của Storage.
        # Lưu ý: `current_time` đã được chuyển vào trong Lua script ở lần review trước.
        result = await self.storage.execute(
            script_name="sliding_window",
            keys=[key],
            args=[self.limit, self.window_size, self.ttl]
        )
        allowed, remaining = result
        # Sliding window không có khái niệm "thời gian chờ", hoặc là được hoặc không.
        return allowed, remaining, 0.0