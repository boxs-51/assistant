import asyncio
import random
import httpx
import structlog

from ..exceptions import (
    ProviderError, 
    ResponseValidationError,
    ProviderRateLimitError,      # Mới thêm: Có thể retry
    ProviderUnavailableError,    # Mới thêm: Có thể retry
    ProviderAuthenticationError  # Mới thêm: Không thể retry
)
from ....circuit_breaker.circuit_breaker import CircuitBreakerOpenError
from ...config import settings

logger = structlog.get_logger(__name__)

class RetryPolicy:
    """
    REFACTORED (v3): Chứa logic về việc thử lại (retry) một cách độc lập.
    Đã cập nhật để nhận diện chính xác các mã lỗi, quota, rate limit từ API Custom Exceptions.
    """
    def __init__(self, max_retries: int | None = None):
        """
        Khởi tạo RetryPolicy.
        :param max_retries: Số lần thử lại tối đa. Nếu là None, sẽ lấy từ cấu hình.
        """
        self.max_retries = max_retries if max_retries is not None else settings.provider.retry

    def _is_retryable(self, error: Exception) -> bool:
        """Kiểm tra xem một lỗi có nên được thử lại hay không dựa trên mã lỗi API."""
        
        # 1. Các lỗi CHẮC CHẮN CÓ THỂ RETRY (Retryable)
        # ---------------------------------------------------------------------
        # Lỗi mạng / Timeout thuần túy từ HTTPX
        if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
            return True
            
        # Lỗi Rate Limit / Quota từ API (HTTP 429 hoặc error_code cụ thể)
        if isinstance(error, ProviderRateLimitError):
            return True
            
        # Lỗi Provider bị sập tạm thời / Gateways (HTTP 502, 503, 504)
        if isinstance(error, ProviderUnavailableError):
            return True
            
        # Xử lý dự phòng nếu lỗi là httpx.HTTPStatusError thuần tuý chưa qua xử lý wrapper
        if isinstance(error, httpx.HTTPStatusError):
            status_code = error.response.status_code
            if status_code == 429 or (500 <= status_code < 600):
                return True
            return False

        # 2. Các lỗi KHÔNG THỂ RETRY (Non-retryable)
        # ---------------------------------------------------------------------
        # Lỗi mạch ngắt (Circuit Breaker OPEN) -> Không gọi API nữa, fail-fast
        if isinstance(error, CircuitBreakerOpenError):
            return False
            
        # Lỗi sai API Key, cấu hình -> Thử lại vô ích
        if isinstance(error, ProviderAuthenticationError):
            return False
            
        # Lỗi validate dữ liệu trả về sai cấu trúc -> Hệ thống hoặc API đang lệch Schema
        if isinstance(error, ResponseValidationError):
            return False
            
        # Các lỗi Provider khác không được định nghĩa cụ thể phía trên (e.g., Lỗi Client 400 Bad Request)
        if isinstance(error, ProviderError):
            return False
            
        # Mặc định các lỗi không rõ nguyên nhân khác (Lỗi logic Code, v.v.) không retry
        return False

    async def apply(self, execution_func, provider_name: str):
        """
        Bọc một hàm thực thi với logic retry.
        """
        for attempt in range(self.max_retries + 1):
            try:
                return await execution_func()
            except Exception as e:
                # Đọc chi tiết thông tin lỗi phục vụ cho việc tracking/logging nếu có
                status_code = getattr(e, 'status_code', None)
                error_code = getattr(e, 'error_code', None)
                
                # Nếu lỗi không thể retry hoặc đã hết số lần thử, ném lại ngay lập tức cho Router/Fallback chain xử lý
                if not self._is_retryable(e) or attempt >= self.max_retries:
                    raise e
                
                # Tính toán thời gian chờ: Exponential backoff với jitter
                delay = (2 ** attempt) + random.uniform(0, 1)
                
                logger.warning(
                    "Retrying provider execution due to transient error.",
                    provider=provider_name,
                    attempt=attempt + 1,
                    max_retries=self.max_retries,
                    delay=round(delay, 2),
                    error_type=e.__class__.__name__,
                    status_code=status_code,
                    error_code=error_code
                )
                
                await asyncio.sleep(delay)
                continue