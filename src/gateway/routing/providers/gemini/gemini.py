import httpx
import structlog
from typing import Dict, Any, AsyncGenerator

# 1. Import các thành phần đã được module hóa
from ..base.provider import BaseProvider
from ..base.auth import ApiKeyInQuery
from ..base.capability import ProviderCapability
from ..base.endpoint import EndpointBuilder
from ..base.model_mapper import ModelMapper
from .adapter import GeminiAdapter # Adapter chuyên biệt

from ....config import settings
from ....schemas import GatewayResponse, GatewayStreamChunk

logger = structlog.get_logger(__name__)

# 2. Định nghĩa các model mapping (có thể chuyển ra file config YAML)
GEMINI_MODEL_MAP = {
    # Gemini 2.5
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",

    # Alias
    "gpt-4o": "gemini-2.5-pro",
    "gpt-4o-mini": "gemini-2.5-flash",

    # Legacy
    "gemini-pro": "gemini-2.5-pro",
    "gemini-1.5-pro": "gemini-2.5-pro",
    "gemini-1.5-pro-latest": "gemini-2.5-pro",
    "gemini-1.5-flash": "gemini-2.5-flash",
    "gemini-1.5-flash-latest": "gemini-2.5-flash",

    "default" : "gemini-2.5-flash"
}

class GeminiProvider(BaseProvider):
    """Nhà cung cấp cho Gemini API, được lắp ráp từ các thành phần chuyên biệt."""
    def __init__(self):
        # 3. Lắp ráp các thành phần (Composition)
        super().__init__(
            provider_name="gemini",
            auth_strategy=ApiKeyInQuery(api_key=str(settings.gemini.api_key), key_name="key"),
            endpoint_builder=EndpointBuilder(base_url=str(settings.gemini.base_url)),
            adapter=GeminiAdapter(),
            model_mapper=ModelMapper(model_map=GEMINI_MODEL_MAP),
            capabilities={
                ProviderCapability.STREAMING,
                ProviderCapability.TEXT_GENERATION,
                ProviderCapability.VISION, # Gemini Pro Vision
                ProviderCapability.STREAMING
            }
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Gemini API key đã được cung cấp hay chưa."""
        return bool(settings.gemini.api_key)

    async def chat(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> GatewayResponse:
        prepared_body = self.prepare_body(body)
        provider_model = prepared_body.get("model", "default") # Lấy model đã được dịch
        
        endpoint_template = "v1beta/models/{model}:{action}"
        action = "generateContent"

        response = await self.send(http_client, endpoint_template, prepared_body, timeout, model=provider_model, action=action)
        return await self.adapter.adapt_chat_response(response)

    async def chat_stream(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> AsyncGenerator[GatewayStreamChunk, None]:
        prepared_body = self.prepare_body(body)
        provider_model = prepared_body.get("model", "default")
        
        endpoint_template = "v1beta/models/{model}:{action}"
        action = "streamGenerateContent"
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        
        response = await self.send(http_client, endpoint_template, prepared_body, timeout, headers, model=provider_model, action=action)
        async for chunk in self.adapter.adapt_chat_stream(response):
            yield chunk