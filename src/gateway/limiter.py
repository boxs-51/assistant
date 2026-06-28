import redis.asyncio as redis
import structlog
from redis.exceptions import ConnectionError
from .rate_limiter.storage.redis_storage import RedisStorage
from .rate_limiter.factory import RateLimiterFactory
from .config import settings

logger = structlog.get_logger(__name__)

class RateLimiterManager:
    """
    REFACTORED: Lớp quản lý chính, đóng vai trò là entry point cho hệ thống.
    Sử dụng RateLimiterFactory để tạo ra thuật toán limiter phù hợp.
    """
    def __init__(self, redis_client: redis.Redis):
        # Dependency Injection: Nhận redis_client từ bên ngoài
        storage = RedisStorage(redis_client)
        # Factory sẽ đọc config và tạo ra limiter tương ứng
        self.limiter = RateLimiterFactory.create_limiter(storage)

    async def is_allowed(self, key: str, cost: int = 1) -> tuple[bool, float]:
        """
        Kiểm tra xem một request có được phép hay không.
        """
        try:
            if not self.limiter:
                # Fail open nếu không có limiter nào được cấu hình
                return True, 0.0
                
            limiter_key = f"rate_limit:{key}"
            allowed, _, wait_time = await self.limiter.is_allowed(key=limiter_key, cost=cost)
            return allowed, wait_time
        except ConnectionError as e:
            logger.error("Rate limiter failed: Could not connect to Redis.", error=str(e))
            # TODO: Add a metric to count Redis failures.
            # if settings.RATE_LIMIT_FAIL_MODE == "closed":
            #     return False, -1
            
            # Default to "fail open" mode
            logger.warning("Failing open: Allowing request due to Redis connection error.")
            return True, 0.0
