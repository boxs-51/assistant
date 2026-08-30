from typing import Tuple, Type
import structlog

from .base import BaseRateLimiter
from ..storage.base import BaseStorage
from .....infrastructure.config.schemas import RateLimitSettings

logger = structlog.get_logger(__name__)

class TokenBucketLimiter(BaseRateLimiter):
    """Triển khai thuật toán Token Bucket."""

    def __init__(self, storage: BaseStorage, capacity: float, refill_rate: float, ttl: int):
        super().__init__(storage)
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.ttl = ttl

    @classmethod
    def from_config(cls: Type["TokenBucketLimiter"], storage: BaseStorage, config: RateLimitSettings) -> "TokenBucketLimiter":
        """Tạo TokenBucketLimiter từ cấu hình ứng dụng."""

        logger.info(
            "Initializing TokenBucketLimiter",
            capacity=config.capacity,
            refill_rate=config.refill_rate,
            ttl=config.cache_expire_seconds
        )

        return cls(storage, config.capacity, config.refill_rate, config.cache_expire_seconds)

    async def is_allowed(self, key: str, cost: int = 1) -> Tuple[bool, int, float]:
        # Thay vì gọi một method cụ thể, giờ đây nó gọi executor chung.
        # Logic về các tham số cần thiết được đóng gói hoàn toàn trong Algorithm.
        # Storage không cần biết `capacity` hay `refill_rate` là gì.
        # Lưu ý: `current_time` đã được chuyển vào trong Lua script ở lần review trước.
        result = await self.storage.execute(
            script_name="token_bucket",
            keys=[key],
            args=[self.capacity, self.refill_rate, float(cost), self.ttl]
        )
        
        allowed, remaining, wait_time = result
        return allowed, int(remaining), wait_time