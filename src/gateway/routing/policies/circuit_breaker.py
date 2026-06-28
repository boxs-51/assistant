import asyncio
import time
from typing import Dict
from enum import Enum
import structlog

logger = structlog.get_logger(__name__)

class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreakerOpenError(Exception):
    """Ngoại lệ được ném ra khi cố gắng thực thi lúc mạch đang mở."""
    pass

class CircuitBreaker:
    """
    Một triển khai Circuit Breaker async-native, an toàn cho môi trường asyncio.
    """
    def __init__(self, failure_threshold: int, reset_timeout: int, success_threshold: int = 1):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.success_threshold = success_threshold
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitBreakerState:
        """Tự động chuyển từ OPEN sang HALF_OPEN nếu đã hết thời gian chờ."""
        if self._state == CircuitBreakerState.OPEN and time.monotonic() - self._last_failure_time > self.reset_timeout:
            return CircuitBreakerState.HALF_OPEN
        return self._state

    async def _on_success(self):
        """Xử lý khi một cuộc gọi thành công."""
        async with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitBreakerState.CLOSED
                    self._failure_count = 0
                    logger.info("Circuit breaker has been closed.", name=getattr(self, 'name', 'unknown'))
            # Nếu đang ở trạng thái CLOSED, reset failure_count
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self):
        """Xử lý khi một cuộc gọi thất bại."""
        async with self._lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.OPEN
                self._last_failure_time = time.monotonic()
                logger.warning("Circuit breaker has been re-opened from half-open state.", name=getattr(self, 'name', 'unknown'))
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitBreakerState.OPEN
                    self._last_failure_time = time.monotonic()
                    logger.error("Circuit breaker has been opened.", name=getattr(self, 'name', 'unknown'))

    async def execute(self, func, *args, **kwargs):
        """Thực thi hàm được bảo vệ bởi Circuit Breaker."""
        if self.state == CircuitBreakerState.OPEN:
            raise CircuitBreakerOpenError("Circuit is open")
        
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure()
            raise e

class CircuitBreakerManager:
    """
    Quản lý tập trung vòng đời của tất cả các đối tượng CircuitBreaker.
    Đảm bảo mỗi provider có một circuit breaker duy nhất.
    """
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        # TODO: Load config từ settings
        self.fail_max = 5
        self.reset_timeout = 30

    def get_breaker(self, provider_name: str) -> CircuitBreaker:
        """Lấy hoặc tạo một CircuitBreaker cho một provider cụ thể."""
        if provider_name not in self._breakers:
            logger.info("Creating new circuit breaker", provider=provider_name)
            breaker = CircuitBreaker(failure_threshold=self.fail_max, reset_timeout=self.reset_timeout)
            setattr(breaker, 'name', provider_name) # Gán tên để logging
            self._breakers[provider_name] = breaker
        return self._breakers[provider_name]