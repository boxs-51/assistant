import httpx
from typing import Dict, Any, AsyncGenerator

from ..base.provider import BaseProvider
from ..base.api import ApiType
from ..base.auth import NoAuth
from ..base.capability import ProviderCapability
from ..base.endpoint import EndpointBuilder
from ..base.model_mapper import ModelMapper
from .adapter import OllamaAdapter

from ....config import settings
from ....schemas import GatewayResponse, GatewayStreamChunk

# Ollama không cần mapping phức tạp, nhưng vẫn giữ cấu trúc để nhất quán
OLLAMA_MODEL_MAP = {}

class OllamaProvider(BaseProvider):
    """Nhà cung cấp cho các mô hình local qua Ollama."""
    def __init__(self):
        super().__init__(
            provider_name="ollama",
            auth_strategy=NoAuth(),
            endpoint_builder=EndpointBuilder(base_url=str(settings.ollama.base_url)),
            adapter=OllamaAdapter(),
            model_mapper=ModelMapper(model_map=OLLAMA_MODEL_MAP),
            capabilities={
                ProviderCapability.STREAMING,
                ProviderCapability.TEXT_GENERATION,
                ProviderCapability.VISION, # Ollama hỗ trợ model vision như llava
            }
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Ollama base URL đã được cung cấp hay chưa."""
        return bool(settings.ollama.base_url)

    async def chat(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> GatewayResponse:
        prepared_body = self.prepare_body(body)
        response = await self.send(http_client, "api/chat", prepared_body, timeout)
        return self.adapter.adapt_chat_response(response)

    async def chat_stream(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> AsyncGenerator[GatewayStreamChunk, None]:
        prepared_body = self.prepare_body(body)
        response = await self.send(http_client, "api/chat", prepared_body, timeout)
        
        async for chunk in self.adapter.adapt_chat_stream(response):
            yield chunk