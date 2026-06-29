import httpx
from typing import Dict, Any, AsyncGenerator

from ..base.provider import BaseProvider
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

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        provider_model = self.mapper.translate(body.get("model"))
        body["model"] = provider_model
        adapted_body = self.adapter.adapt_request(body)
        request_url = self.endpoints.build("api/chat")
        final_url, auth_headers = self.auth.prepare_request(request_url, {"Content-Type": "application/json"})
        return await http_client.post(final_url, json=adapted_body, headers=auth_headers, timeout=timeout)

    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        response.raise_for_status()
        return self.adapter.adapt_response(response.json(), model)

    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        async for chunk in self.adapter.adapt_stream(response.aiter_bytes(), model):
            yield chunk