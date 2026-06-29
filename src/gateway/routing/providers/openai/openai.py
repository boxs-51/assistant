import httpx
from typing import Dict, Any, AsyncGenerator

from ..base.provider import BaseProvider
from ..base.auth import BearerToken
from ..base.capability import ProviderCapability
from ..base.endpoint import EndpointBuilder
from ..base.model_mapper import ModelMapper
from .adapter import OpenAIAdapter

from ....config import settings
from ....schemas import GatewayResponse, GatewayStreamChunk

# Mapping model cho OpenAI, có thể mở rộng cho các model fallback
OPENAI_MODEL_MAP = {
    "gpt-4o": "gpt-4o",
    "gpt-4-turbo": "gpt-4-turbo",
    "gpt-3.5-turbo": "gpt-3.5-turbo",
    # Fallback mapping
    "gemini-1.5-pro": "gpt-4o",
}

class OpenAIProvider(BaseProvider):
    """Nhà cung cấp cho các mô hình của OpenAI hoặc các API tương thích OpenAI."""
    def __init__(self):
        super().__init__(
            provider_name="openai",
            auth_strategy=BearerToken(api_key=str(settings.openai.api_key)),
            endpoint_builder=EndpointBuilder(base_url=str(settings.openai.base_url)),
            adapter=OpenAIAdapter(),
            model_mapper=ModelMapper(model_map=OPENAI_MODEL_MAP),
            capabilities={
                ProviderCapability.STREAMING,
                ProviderCapability.TEXT_GENERATION,
                ProviderCapability.VISION,
                ProviderCapability.TOOL_CALLING,
            }
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem OpenAI API key đã được cung cấp hay chưa."""
        return bool(settings.openai.api_key)

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        """Thực hiện request đến OpenAI API bằng cách sử dụng các thành phần đã được lắp ráp."""
        provider_model = self.mapper.translate(body.get("model", "gpt-4o"))
        body["model"] = provider_model # Cập nhật model trong body request
        adapted_body = self.adapter.adapt_request(body)
        request_url = self.endpoints.build("chat/completions")
        final_url, auth_headers = self.auth.prepare_request(request_url, {"Content-Type": "application/json"})
        return await http_client.post(final_url, json=adapted_body, headers=auth_headers, timeout=timeout)

    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        response.raise_for_status()
        return self.adapter.adapt_response(response.json(), model)

    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        async for chunk in self.adapter.adapt_stream(response.aiter_bytes(), model):
            yield chunk