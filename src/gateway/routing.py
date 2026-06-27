import httpx
from typing import List, Dict, Any, Tuple
from abc import ABC, abstractmethod
from pybreaker import CircuitBreaker, CircuitBreakerError

from .config import settings
from .observability import metrics

class NoAvailableProviderError(Exception):
    """Ngoại lệ được ném ra khi tất cả các provider trong chuỗi fallback đều thất bại."""
    pass

class BaseProvider(ABC):
    """Lớp cơ sở trừu tượng cho một nhà cung cấp LLM."""
    def __init__(self, name: str, fail_max: int = 5, reset_timeout: int = 30):
        self.name = name
        # Mỗi provider có một Circuit Breaker riêng.
        # Nếu có 5 lỗi trong vòng 30 giây, nó sẽ "mở" (ngắt mạch) và ngừng nhận request.
        self.breaker = CircuitBreaker(fail_max=fail_max, reset_timeout=reset_timeout)

    @abstractmethod
    async def _make_request(self, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> httpx.Response:
        """Phương thức nội bộ để thực hiện việc gọi API cụ thể."""
        pass

    @breaker
    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> httpx.Response:
        """
        Wrapper an toàn để gọi API, được bảo vệ bởi Circuit Breaker.
        Decorator @breaker sẽ tự động xử lý việc mở/đóng mạch.
        """
        response = await self._make_request(http_client, body)
        # Nếu status code là lỗi server (>=500), ném ngoại lệ để Circuit Breaker ghi nhận là một lỗi.
        if response.status_code >= 500:
            metrics.increment_provider_errors(self.name, str(response.status_code))
            response.raise_for_status()
        return response

class OpenAIProvider(BaseProvider):
    """Nhà cung cấp cho OpenAI API."""
    def __init__(self):
        super().__init__(name="openai")
        self.api_url = "https://api.openai.com/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }

    async def _make_request(self, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> httpx.Response:
        return await http_client.post(self.api_url, json=body, headers=self.headers)

class AzureOpenAIProvider(BaseProvider):
    """Nhà cung cấp cho Azure OpenAI (giả lập)."""
    def __init__(self):
        super().__init__(name="azure_openai")
        # Thay thế bằng endpoint thực tế của bạn
        self.api_url = f"{settings.AZURE_ENDPOINT}/openai/deployments/{settings.AZURE_DEPLOYMENT}/chat/completions?api-version=2024-02-01"
        self.headers = {
            "api-key": settings.AZURE_API_KEY,
            "Content-Type": "application/json"
        }

    async def _make_request(self, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> httpx.Response:
        # Azure có thể yêu cầu một số thay đổi trong body, xử lý ở đây nếu cần
        return await http_client.post(self.api_url, json=body, headers=self.headers)


class ModelRouter:
    """
    Lớp điều phối chính, quản lý việc định tuyến, fallback và circuit breaking.
    """
    def __init__(self):
        self.providers = {
            "openai": OpenAIProvider(),
            "azure": AzureOpenAIProvider(),
            # Thêm các provider khác ở đây
        }
        # Định nghĩa chuỗi fallback cho từng model
        self.routing_table: Dict[str, List[BaseProvider]] = {
            "default": [self.providers["openai"], self.providers["azure"]],
            "gpt-4o": [self.providers["openai"], self.providers["azure"]],
            "gpt-3.5-turbo": [self.providers["openai"], self.providers["azure"]],
            "azure-gpt-4o": [self.providers["azure"], self.providers["openai"]],
        }

    async def execute_with_fallback(
        self, http_client: httpx.AsyncClient, model: str, body: Dict[str, Any]
    ) -> Tuple[httpx.Response, BaseProvider]:
        """
        Thực thi request với cơ chế fallback và circuit breaker.
        Hàm sẽ lặp qua các provider trong chuỗi fallback cho đến khi tìm được một provider hoạt động.
        """
        fallback_chain = self.routing_table.get(model, self.routing_table["default"])
        last_exception = None

        for provider in fallback_chain:
            try:
                print(f"ℹ️ [Router] Đang thử gọi provider: {provider.name}")
                # Gọi hàm request() đã được bọc bởi circuit breaker
                response = await provider.request(http_client, body)
                print(f"✅ [Router] Provider '{provider.name}' phản hồi thành công.")
                return response, provider
            except CircuitBreakerError as e:
                print(f"CircuitBreakerError: {e}")
                print(f"🟡 [Router] Circuit Breaker cho '{provider.name}' đang mở. Chuyển sang provider tiếp theo.")
                metrics.increment_circuit_breaker_opens(provider.name)
                last_exception = e
                continue
            except (httpx.HTTPError, Exception) as e:
                print(f"🔴 [Router] Provider '{provider.name}' thất bại: {e}. Chuyển sang provider tiếp theo.")
                last_exception = e
                continue

        # Nếu tất cả các provider đều thất bại
        print("🛑 [Router] Tất cả các provider trong chuỗi fallback đều thất bại.")
        raise NoAvailableProviderError("All providers are currently unavailable.") from last_exception