from typing import Dict, Any

import httpx
import structlog

from ..config import settings
from ..metrics import metrics
from .exceptions import ProviderError
from .providers.base import BaseProvider
from .policies.retry import RetryPolicy
from .policies.circuit_breaker import CircuitBreakerManager, CircuitBreakerOpenError

logger = structlog.get_logger(__name__)

class ProviderExecutor:
    """
    REFACTORED: Lớp điều phối việc thực thi một request đến một provider duy nhất.
    Nó áp dụng một chuỗi các chính sách (policies) như Retry và Circuit Breaker.
    """
    def __init__(self, circuit_breaker_manager: CircuitBreakerManager):
        # Dependency Injection: Nhận các manager/policy từ bên ngoài
        self.breaker_manager = circuit_breaker_manager
        self.retry_policy = RetryPolicy()

    async def execute(self, provider: BaseProvider, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> httpx.Response:
        """
        Thực thi một request bằng cách áp dụng chuỗi chính sách.
        Chain of Responsibility: RetryPolicy -> CircuitBreaker -> Actual Call
        """
        async def execution_func():
            """Hàm thực thi lõi, được bọc bởi các policy."""
            breaker = self.breaker_manager.get_breaker(provider.name)
            try:
                # Áp dụng Circuit Breaker tự xây dựng
                response = await breaker.execute(provider.request, http_client, body, settings.PROVIDER_TIMEOUT)
                # Kiểm tra lỗi HTTP sau khi gọi
                response.raise_for_status()
                return response
            except CircuitBreakerOpenError as e:
                logger.warning("Circuit breaker is open", provider=provider.name, error=str(e))
                metrics.increment_circuit_breaker_opens(provider.name)
                raise ProviderError(f"Circuit breaker is open for {provider.name}", provider.name) from e
            except httpx.HTTPStatusError as e:
                metrics.increment_provider_errors(provider.name, str(e.response.status_code))
                raise e # Ném lại lỗi gốc để RetryPolicy quyết định
            except httpx.RequestError as e:
                metrics.increment_provider_errors(provider.name, "timeout")
                raise e # Ném lại lỗi gốc để RetryPolicy quyết định

        # Áp dụng Retry Policy, bọc bên ngoài cùng
        return await self.retry_policy.apply(execution_func, provider.name)