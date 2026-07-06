import redis.asyncio as redis
import structlog
from redis.exceptions import RedisError
from .storage.redis_storage import RedisStorage
from .factory import RateLimiterFactory
from ..config import settings
from ..circuit_breaker import CircuitBreakerManager, CircuitBreakerOpenError
from .. import observability as gateway_metrics

logger = structlog.get_logger(__name__)

class RateLimiterManager:
    """
    REFACTORED: Lớp quản lý chính, đóng vai trò là entry point cho hệ thống.
    Sử dụng RateLimiterFactory để tạo ra thuật toán limiter phù hợp.
    """
    def __init__(self, redis_client: redis.Redis, circuit_breaker_manager: CircuitBreakerManager):
        # Dependency Injection: Nhận redis_client từ bên ngoài
        storage = RedisStorage(redis_client)
        # --- MỚI: Nhận CircuitBreakerManager từ bên ngoài ---
        self.circuit_breaker_manager = circuit_breaker_manager
        # Factory sẽ đọc config và tạo ra limiter tương ứng.
        # Truyền cả hai phần của config để factory có thể linh hoạt.
        self.limiter = RateLimiterFactory.create_limiter(
            algorithm=settings.rate_limit.algorithm.lower(),
            storage=storage,
            config=settings # Truyền toàn bộ object config
        )

    async def is_allowed(self, key: str, cost: int = 1) -> tuple[bool, float]:
        """
        Kiểm tra xem một request có được phép hay không.
        """
        # Lấy breaker cho Redis rate limiter từ manager
        breaker = await self.circuit_breaker_manager.get_breaker("redis_rate_limiter")

        try:
            # 1. Kiểm tra trạng thái của breaker trước khi thực hiện
            await breaker.before_request()

            # 2. Thực thi logic chính
            result = await self._execute_check(key, cost)

            # 3. Thông báo thành công cho breaker
            await breaker.on_success()
            return result
        except CircuitBreakerOpenError:
            # Mạch đang mở, xử lý ngay lập tức theo chiến lược fail-over
            logger.warning("Circuit breaker is open for Redis. Applying fail-over strategy.")
            return self._apply_fail_over_strategy()
        except RedisError as e:
            # 4. Thông báo thất bại cho breaker
            await breaker.on_failure()
            return self._apply_fail_over_strategy()

    def _apply_fail_over_strategy(self) -> tuple[bool, float]:
        """Áp dụng chiến lược fail-open/fail-closed khi có lỗi."""
        if settings.rate_limit.fail_mode == "closed":
            logger.warning("Failing closed: Rejecting request due to Redis error or open circuit.")
            return False, -1.0

        logger.warning("Failing open: Allowing request due to Redis error or open circuit.")
        return True, 0.0

    async def _execute_check(self, key: str, cost: int = 1) -> tuple[bool, float]:
        """Hàm chứa logic cốt lõi, sẽ được Circuit Breaker gọi."""
        if not self.limiter:
            # Fail open nếu không có limiter nào được cấu hình.
            return True, 0.0
            
        limiter_key = f"{settings.redis.key_prefix}:rate_limit:{key}"
        allowed, _, wait_time = await self.limiter.is_allowed(key=limiter_key, cost=cost)

        # Ghi nhận metrics
        if not allowed:
            gateway_metrics.metrics.increment_rate_limit()

        return allowed, wait_time
