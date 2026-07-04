import httpx
from typing import Dict, Any, AsyncGenerator

from ..base.provider import BaseProvider
from ..base.api import ApiType
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

    async def chat(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> GatewayResponse:
        prepared_body = self.prepare_body(body, default_model="gpt-4o")
        response = await self.send(http_client, ApiType.CHAT_COMPLETIONS, prepared_body, timeout)
        return self.adapter.adapt_chat_response(response)


    async def chat_stream(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> AsyncGenerator[GatewayStreamChunk, None]:
        prepared_body = self.prepare_body(body, default_model="gpt-4o")
        response = await self.send(http_client, ApiType.CHAT_COMPLETIONS, prepared_body, timeout)
        
        async for chunk in self.adapter.adapt_chat_stream(response):
            yield chunk