from typing import Dict, Any, Tuple, AsyncGenerator

import httpx
import structlog

from ..config import settings
from ..import observability as gateway_metrics
from .exceptions import ProviderError
from .providers.base.provider import BaseProvider
from ..schemas import GatewayResponse, GatewayStreamChunk # Import schema chuẩn
from .policies.retry import RetryPolicy
from ...circuit_breaker.circuit_breaker import CircuitBreakerManager, CircuitBreakerOpenError

logger = structlog.get_logger(__name__)

class ProviderExecutor:
    """
    REFACTORED (v2): Lớp điều phối việc thực thi một request đến một provider duy nhất.
    Nó điều phối các policy theo đúng kiến trúc resilience (Polly, Resilience4j).
    """
    def __init__(self, circuit_breaker_manager: CircuitBreakerManager):
        # Dependency Injection: Nhận các manager/policy từ bên ngoài
        self.breaker_manager = circuit_breaker_manager
        self.retry_policy = RetryPolicy()

    async def is_provider_healthy(self, provider_name: str) -> bool:
        """Kiểm tra xem circuit breaker của provider có đang mở hay không."""
        breaker = await self.breaker_manager.get_breaker(provider_name)
        return not await breaker.is_open()

    def _get_error_metric_label(self, error: httpx.RequestError) -> str:
        """Phân loại lỗi httpx để ghi nhận metric chính xác."""
        if isinstance(error, httpx.ConnectError):
            return "connect_error"
        if isinstance(error, httpx.ReadTimeout):
            return "read_timeout"
        if isinstance(error, httpx.WriteTimeout):
            return "write_timeout"
        if isinstance(error, httpx.PoolTimeout):
            return "pool_timeout"
        return "request_error"

    async def execute(self, provider: BaseProvider, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> GatewayResponse:
        """
        Điều phối việc thực thi request với các policy Circuit Breaker và Retry.
        Luồng thực thi: before_request -> retry(request -> normalize) -> on_success/on_failure.
        """
        breaker = await self.breaker_manager.get_breaker(provider.name)
        model = body.get("model", "unknown")

        async def execution_func():
            """Hàm thực thi lõi, chỉ gọi provider và kiểm tra status."""
            response = await provider.request(http_client, body, settings.provider.timeout)
            # Bước mới: Chuẩn hóa response ngay sau khi nhận được
            normalized_response = await provider.normalize_response(response, model)
            return normalized_response

        try:
            # Bước 1 & 3: Kiểm tra breaker trước, sau đó thực thi logic retry.
            # Toàn bộ logic on_success/on_failure nằm ngoài RetryPolicy.
            await breaker.before_request()

            # Bước 2: RetryPolicy chỉ bọc hàm thực thi lõi.
            response = await self.retry_policy.apply(execution_func, provider.name)

            # Bước 4 (Success): Nếu retry thành công, ghi nhận success cho breaker.
            await breaker.on_success()
            return response

        except CircuitBreakerOpenError as e:
            # Bước 5: Chuyển đổi CircuitBreakerOpenError thành ProviderError để Router có thể fallback.
            logger.warning("Skipping provider call, circuit breaker is open.", provider=provider.name)
            raise ProviderError(f"Circuit breaker is open for {provider.name}", provider_name=provider.name) from e

        except Exception as e:
            # Bước 4 (Failure): Nếu retry thất bại (hết số lần thử), ghi nhận failure cho breaker.
            await breaker.on_failure()

            # Bước 8 & 9: Ghi nhận metrics và log sau khi đã retry thất bại.
            if isinstance(e, httpx.HTTPStatusError):
                error_label = str(e.response.status_code)
            elif isinstance(e, httpx.RequestError):
                error_label = self._get_error_metric_label(e)
            else:
                error_label = "unexpected_error"
            
            gateway_metrics.metrics.increment_provider_errors(provider.name, error_label)
            logger.warning(
                "Provider execution failed after all retries.",
                provider=provider.name, error=str(e), error_type=type(e).__name__
            )
            # Ném lại lỗi dưới dạng ProviderError để Router có thể fallback.
            raise ProviderError(f"Provider failed after all retries: {e}", provider_name=provider.name) from e

    async def execute_stream(self, provider: BaseProvider, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> AsyncGenerator[GatewayStreamChunk, None]:
        """
        Điều phối việc thực thi một request streaming.
        Luồng này không hỗ trợ retry cho từng chunk.
        """
        breaker = await self.breaker_manager.get_breaker(provider.name)
        model = body.get("model", "unknown")

        try:
            await breaker.before_request()

            # Gọi thẳng vào provider.request, không qua retry policy cho stream
            response = await provider.request(http_client, body, settings.provider.timeout)
            
            # Bắt đầu stream và chuẩn hóa
            async for chunk in provider.normalize_stream(response, model):
                yield chunk

            await breaker.on_success()

        except CircuitBreakerOpenError as e:
            logger.warning("Skipping provider stream, circuit breaker is open.", provider=provider.name)
            raise ProviderError(f"Circuit breaker is open for {provider.name}", provider_name=provider.name) from e

        except Exception as e:
            await breaker.on_failure()
            if isinstance(e, httpx.HTTPStatusError):
                error_label = str(e.response.status_code)
            elif isinstance(e, httpx.RequestError):
                error_label = self._get_error_metric_label(e)
            else:
                error_label = "unexpected_error"
            
            gateway_metrics.metrics.increment_provider_errors(provider.name, error_label)
            logger.warning(
                "Provider stream execution failed.", provider=provider.name, error=str(e), error_type=type(e).__name__
            )
            raise ProviderError(f"Provider stream failed: {e}", provider_name=provider.name) from e