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
            }
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Gemini API key đã được cung cấp hay chưa."""
        return bool(settings.gemini.api_key)

    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        # 4. Logic request giờ đây trở nên cực kỳ đơn giản và chuẩn hóa
        provider_model = self.mapper.translate(body.get("model") or "default")
        adapted_body = self.adapter.adapt_request(body)
        is_streaming = body.get("stream", False)
        endpoint_template = "v1beta/models/{model}:{action}"
        action = "streamGenerateContent" if is_streaming else "generateContent"
        request_url = self.endpoints.build(endpoint_template, model=provider_model, action=action)
        headers = {"Content-Type": "application/json"}
        if is_streaming:
            headers["Accept"] = "text/event-stream"
        # Auth được xử lý tự động
        final_url, auth_headers = self.auth.prepare_request(request_url, headers)
        logger.info(
            "Gemini request",
            provider_model=provider_model,
            url=request_url,
            stream=is_streaming,
        )
                
        return await http_client.post(final_url, json=adapted_body, headers=auth_headers, timeout=timeout)

    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        response.raise_for_status()
        return self.adapter.adapt_response(response.json(), model)

    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        response.raise_for_status()
        async for chunk in self.adapter.adapt_stream(response.aiter_bytes(), model):
            yield chunk