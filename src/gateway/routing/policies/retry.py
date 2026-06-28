import asyncio
import random
import httpx
import structlog

from ...config import settings

logger = structlog.get_logger(__name__)

class RetryPolicy:
    """
    Strategy Pattern: Chứa logic về việc thử lại (retry) một cách độc lập.
    """
    def __init__(self, max_retries: int = settings.PROVIDER_RETRY):
        self.max_retries = max_retries

    def _is_retryable(self, error: Exception) -> bool:
        """Kiểm tra xem một lỗi có nên được thử lại hay không."""
        if isinstance(error, httpx.HTTPStatusError):
            # Chỉ retry với lỗi server (5xx) hoặc 429 (Too Many Requests)
            return error.response.status_code >= 500 or error.response.status_code == 429
        if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
            # Lỗi kết nối hoặc timeout là có thể thử lại
            return True
        # Không retry với các lỗi client-side khác hoặc lỗi đã được Circuit Breaker bắt
        return False

    async def apply(self, execution_func, provider_name: str):
        """
        Bọc một hàm thực thi với logic retry.
        """
        for attempt in range(self.max_retries + 1):
            try:
                return await execution_func()
            except Exception as e:
                if self._is_retryable(e) and attempt < self.max_retries:
                    delay = (2 ** attempt) + random.uniform(0, 1)
                    status_code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else "N/A"
                    logger.warning(
                        "Provider execution failed, retrying...",
                        provider=provider_name, attempt=attempt + 1, status=status_code,
                        error=str(e), delay=round(delay, 2)
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise e