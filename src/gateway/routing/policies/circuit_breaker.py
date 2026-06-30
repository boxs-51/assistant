import asyncio
import time
from typing import Dict, Optional
from enum import Enum
import structlog

from ... import observability as gateway_metrics

logger = structlog.get_logger(__name__)

class CircuitBreakerState(Enum):
    OPEN = "open"
    HALF_OPEN = "half_open"
    CLOSED = "closed"

class CircuitBreakerOpenError(Exception):
    """Ngoại lệ được ném ra khi cố gắng thực thi lúc mạch đang mở."""
    pass

class CircuitBreaker:
    """
    REFACTORED: Một State Machine cho Circuit Breaker, an toàn cho môi trường asyncio.
    Nó chỉ quản lý trạng thái và không trực tiếp thực thi request.
    """
    def __init__(self, provider_name: str, failure_threshold: int, reset_timeout: int, success_threshold: int = 1):
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.success_threshold = success_threshold
        
        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        
        # Lock để đảm bảo các thao tác cập nhật state là an toàn
        self._state_lock = asyncio.Lock()
        # Lock riêng cho HALF_OPEN để chỉ một request được phép thử
        self._half_open_lock = asyncio.Lock()

    @property
    async def state(self) -> CircuitBreakerState:
        """
        Lấy trạng thái hiện tại của circuit breaker.
        Tự động chuyển từ OPEN sang HALF_OPEN nếu đã hết thời gian chờ.
        """
        async with self._state_lock:
            if self._state == CircuitBreakerState.OPEN and self._is_reset_timeout_expired():
                self._state = CircuitBreakerState.HALF_OPEN
                self._success_count = 0 # Reset success count for the new trial
                logger.warning("Circuit breaker is now HALF_OPEN. Allowing a trial request.", provider=self.provider_name)
            return self._state

    def _is_reset_timeout_expired(self) -> bool:
        """Kiểm tra xem thời gian reset đã hết hạn hay chưa."""
        return time.monotonic() - self._last_failure_time > self.reset_timeout

    async def before_request(self):
        """
        Phải được gọi trước mỗi request.
        Ném ra CircuitBreakerOpenError nếu request không được phép.
        """
        current_state = await self.state

        if current_state == CircuitBreakerState.OPEN:
            raise CircuitBreakerOpenError(f"Circuit is open for provider {self.provider_name}")

        if current_state == CircuitBreakerState.HALF_OPEN:
            # Chỉ cho phép một coroutine duy nhất đi qua ở trạng thái HALF_OPEN
            if not self._half_open_lock.locked():
                await self._half_open_lock.acquire()
                # Nếu coroutine này thành công, nó sẽ release lock trong on_success
                # Nếu thất bại, nó sẽ release lock trong on_failure
            else:
                # Các coroutine khác đến trong khi đang thử sẽ bị từ chối
                raise CircuitBreakerOpenError(f"Circuit is half-open and a trial request is in progress for {self.provider_name}")

    async def on_success(self):
        """Phải được gọi khi một request thành công."""
        async with self._state_lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitBreakerState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info("Circuit breaker has been CLOSED due to successful trial.", provider=self.provider_name)
                    # Release lock sau khi đã chuyển trạng thái thành công
                    if self._half_open_lock.locked():
                        self._half_open_lock.release()
            # Nếu đang ở trạng thái CLOSED, reset failure_count về 0
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count = 0

    async def on_failure(self):
        """Phải được gọi khi một request thất bại."""
        async with self._state_lock:
            if self._state == CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.OPEN
                self._last_failure_time = time.monotonic()
                logger.warning("Circuit breaker has been RE-OPENED from half-open state due to trial failure.", provider=self.provider_name)
                gateway_metrics.metrics.increment_circuit_breaker_opens(self.provider_name)
                # Release lock sau khi thử thất bại
                if self._half_open_lock.locked():
                    self._half_open_lock.release()
            elif self._state == CircuitBreakerState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitBreakerState.OPEN
                    self._last_failure_time = time.monotonic()
                    logger.error("Circuit breaker has been OPENED due to failures.", provider=self.provider_name, failure_count=self._failure_count)
                    gateway_metrics.metrics.increment_circuit_breaker_opens(self.provider_name)

    async def is_open(self) -> bool:
        """Kiểm tra nhanh xem circuit có đang mở hay không, dùng cho health-aware routing."""
        current_state = await self.state
        return current_state == CircuitBreakerState.OPEN

    @property
    def current_state(self) -> CircuitBreakerState:
        """Trả về trạng thái nội bộ mà không kích hoạt logic chuyển đổi."""
        return self._state

class CircuitBreakerManager:
    """
    Quản lý tập trung vòng đời của tất cả các đối tượng CircuitBreaker.
    Đảm bảo mỗi provider có một circuit breaker duy nhất.
    """
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
        # TODO: Load config từ settings
        self.fail_max = 5
        self.reset_timeout = 30

    async def get_breaker(self, provider_name: str) -> CircuitBreaker:
        """Lấy hoặc tạo một CircuitBreaker cho một provider cụ thể."""
        # Double-checked locking pattern (async version)
        if provider_name not in self._breakers:
            async with self._lock:
                if provider_name not in self._breakers:
                    logger.info("Creating new circuit breaker", provider=provider_name)
                    breaker = CircuitBreaker(provider_name=provider_name, failure_threshold=self.fail_max, reset_timeout=self.reset_timeout)
                    self._breakers[provider_name] = breaker
        return self._breakers[provider_name]