import asyncio
import random
import httpx
import structlog

from ..exceptions import ProviderError,  ResponseValidationError
from .circuit_breaker import CircuitBreakerOpenError
from ...config import settings

logger = structlog.get_logger(__name__)

class RetryPolicy:
    """
    REFACTORED (v2): Chứa logic về việc thử lại (retry) một cách độc lập.
    Nó không biết về Circuit Breaker, Metrics hay các thành phần khác.
    """
    def __init__(self, max_retries: int = settings.PROVIDER_RETRY):
        self.max_retries = max_retries

    def _is_retryable(self, error: Exception) -> bool:
        """Kiểm tra xem một lỗi có nên được thử lại hay không."""
        # Bước 7: Phân loại lỗi có thể và không thể retry.
        
        # Các lỗi không thể retry (non-retryable)
        if isinstance(error, (CircuitBreakerOpenError, ResponseValidationError, ProviderError)):
            return False
        if isinstance(error, httpx.HTTPStatusError):
            # Lỗi client (4xx) thường không thể retry, trừ 429.
            # Lỗi server (5xx) có thể retry.
            is_client_error = 400 <= error.response.status_code < 500
            is_too_many_requests = error.response.status_code == 429
            return not is_client_error or is_too_many_requests
        
        # Các lỗi có thể retry (retryable)
        if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
            return True
        return False

    async def apply(self, execution_func, provider_name: str):
        """
        Bọc một hàm thực thi với logic retry.
        """
        for attempt in range(self.max_retries + 1):
            try:
                return await execution_func()
            except Exception as e:
                # Nếu lỗi không thể retry hoặc đã hết số lần thử, ném lại ngay lập tức.
                if not self._is_retryable(e) or attempt >= self.max_retries:
                    raise e
                else:
                    # Exponential backoff with jitter
                    delay = (2 ** attempt) + random.uniform(0, 1)
                    # Logging được chuyển ra ProviderExecutor, ở đây chỉ sleep.
                    logger.debug(
                        "Retrying provider execution.",
                        provider=provider_name, attempt=attempt + 1, delay=round(delay, 2)
                    )
                    await asyncio.sleep(delay)
                    continue