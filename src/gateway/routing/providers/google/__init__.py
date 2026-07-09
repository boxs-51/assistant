import httpx
import structlog
import base64
import asyncio
import os

from typing import Dict, Any, AsyncGenerator, List, Optional

# 1. Import các thành phần đã được module hóa
from ..core import (
    BaseProvider, ApiType,
    ApiKeyInQuery, ApiTypeMapper, BearerToken,
    EndpointBuilder,
    ModelCapabilityManager, ProviderCapability,
    ModelMapper
)
from ....config import settings
from .api import GoogleChat, GoogleFiles, GoogleModels, GoogleEmbeddings

logger = structlog.get_logger(__name__)

# 2. Định nghĩa các model mapping (có thể chuyển ra file config YAML)
GOOGLE_MODEL_MAP = {
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

# Ánh xạ ApiType sang endpoint template của Gemini
GOOGLE_API_MAP = {
    ApiType.CHAT_COMPLETIONS: "v1beta/models/{model}:{action}",
    ApiType.MODELS: "v1beta/models",
    ApiType.MODEL: "v1beta/models/{model}",
    ApiType.EMBEDDINGS: "v1beta/models/{model}:{action}",
    ApiType.IMAGE_GENERATION: "v1/images:generate", # Giả định endpoint cho Imagen 2
    ApiType.TEXT_TO_SPEECH: "v1/text:synthesize", # Giả định endpoint cho Text-to-Speech
    ApiType.FILES : "v1beta/files", # Endpoint cho File API
}

class GoogleProvider(BaseProvider):
    """Nhà cung cấp cho Gemini API, được lắp ráp từ các thành phần chuyên biệt."""
    def __init__(self):
        # 3. Lắp ráp các thành phần (Composition)
        super().__init__(
            provider_name="gemini",
            auth_strategy=ApiKeyInQuery(api_key=str(settings.gemini.api_key), key_name="key"),
            endpoint_builder=EndpointBuilder(base_url=str(settings.gemini.base_url)),
            api_mapper=ApiTypeMapper(api_map=GOOGLE_API_MAP),
            model_mapper=ModelMapper(model_map=GOOGLE_MODEL_MAP),
            capability_manager=ModelCapabilityManager(provider_name="gemini"), # Có thể tạo GeminiCapabilityManager riêng sau này
            provider_capabilities={
                ProviderCapability.BATCH_API, # Gemini hỗ trợ batch embeddings
                ProviderCapability.FINE_TUNING,
            }
        )
        self.chat = GoogleChat(provider=self)
        self.files = GoogleFiles(provider=self)
        self.models = GoogleModels(provider=self)
        self.embeddings = GoogleEmbeddings(provider=self)

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Gemini API key đã được cung cấp hay chưa."""
        return bool(settings.gemini.api_key)


    async def image_generation(self, **kwargs) -> Dict[str, Any]:
        """Tạo hình ảnh từ văn bản bằng API của Gemini (Imagen)."""
        body = kwargs.get("body")
        # Chuyển đổi request sang định dạng của Gemini
        adapted_body = self.adapter.adapt_image_generation_request(body)

        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.IMAGE_GENERATION,
            json=adapted_body,
            timeout=kwargs.get("timeout"),
        )
        # Chuyển đổi response về định dạng chuẩn của Gateway (giống OpenAI)
        return await self.adapter.adapt_image_generation_response(response)
    

    async def moderation(self, **kwargs) -> Any: raise NotImplementedError
    async def computer_use(self, **kwargs) -> Any: raise NotImplementedError
    async def provider_info(self, **kwargs) -> Any: raise NotImplementedError
    async def health(self, **kwargs) -> Any: raise NotImplementedError
    # ... and so on for all other abstract methods defined in BaseProvider.