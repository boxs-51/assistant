import structlog
from redis.exceptions import RedisError
from .storage.redis_storage import RedisStorage
from .factory import RateLimiterFactory
from ..circuit_breaker import CircuitBreakerManager, CircuitBreakerOpenError
from ...storage.interfaces.cache import CacheDriver
from ...schemas.identity import Identity
from ..middleware.observability import  gateway_metrics

from ...config import settings

logger = structlog.get_logger(__name__)

class RateLimiterManager:
    """
    REFACTORED: Lớp quản lý chính, đóng vai trò là entry point cho hệ thống.
    Sử dụng RateLimiterFactory để tạo ra thuật toán limiter phù hợp.
    """
    def __init__(self, cache_driver: CacheDriver, circuit_breaker_manager: CircuitBreakerManager):
        # Dependency Injection: Nhận CacheDriver từ bên ngoài
        storage = RedisStorage(cache_driver) # Tạm thời vẫn dùng raw client
        self.circuit_breaker_manager = circuit_breaker_manager
        # Factory sẽ đọc config và tạo ra limiter tương ứng.
        # Truyền cả hai phần của config để factory có thể linh hoạt.
        self.config = settings
        self.limiter = RateLimiterFactory.create_limiter(
            algorithm=self.config.rate_limit.algorithm.lower(),
            storage=storage,
            config=self.config # Truyền toàn bộ object config
        )

    async def is_allowed(self, identity: Identity, cost: int = 1) -> tuple[bool, float]:
        """
        Kiểm tra xem một request có được phép hay không.
        """
        # Nếu Redis không được kết nối, tạm thời cho qua và ghi log.
        if not self.limiter or not self.limiter.storage.redis:
            logger.warning("Rate limiter is bypassed because Redis is not connected.")
            return True, 0.0

        # Lấy breaker cho Redis rate limiter từ manager
        breaker = await self.circuit_breaker_manager.get_breaker("redis_rate_limiter")

        try:
            # 1. Kiểm tra trạng thái của breaker trước khi thực hiện
            await breaker.before_request()

            # 2. Thực thi logic chính với identity
            result = await self._execute_check(identity, cost)

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
        if self.config.rate_limit.fail_mode == "closed":
            logger.warning("Failing closed: Rejecting request due to Redis error or open circuit.")
            return False, -1.0

        logger.warning("Failing open: Allowing request due to Redis error or open circuit.")
        return True, 0.0

    async def _execute_check(self, identity: Identity, cost: int = 1) -> tuple[bool, float]:
        """Hàm chứa logic cốt lõi, sẽ được Circuit Breaker gọi."""
        if not self.limiter:
            # Fail open nếu không có limiter nào được cấu hình.
            return True, 0.0
        
        # Tạo key dựa trên plan và định danh của người dùng
        identifier = identity.get_rate_limit_key()
        limiter_key = f"{self.config.redis.key_prefix}:rate_limit:plan:{identity.plan}:{identifier}"
        allowed, _, wait_time = await self.limiter.is_allowed(key=limiter_key, cost=cost)

        # Ghi nhận metrics
        if not allowed:
            gateway_metrics.metrics.increment_rate_limit()

        return allowed, wait_time
