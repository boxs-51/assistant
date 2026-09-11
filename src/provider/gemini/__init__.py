import httpx
import structlog
import base64
import asyncio
import os

from typing import Dict, Any, AsyncGenerator, List, Optional

# 1. Import các thành phần đã được module hóa
from ..core import (
    BaseProvider,
    ApiKeyHeader, ApiTypeMapper,
    EndpointBuilder,
    ModelCapabilityManager, ProviderCapability,
    ModelMapper
)
from ...infrastructure.config.schemas import ProviderConfig
from .api import GeminiChat, GeminiFiles, GeminiModels, GeminiEmbeddings
from .mapper import GEMINI_MODEL_MAP, GEMINI_API_MAP
logger = structlog.get_logger(__name__)

class GeminiProvider(BaseProvider):
    """Nhà cung cấp cho Gemini API, được lắp ráp từ các thành phần chuyên biệt."""
    def __init__(self, config: ProviderConfig):
        # 3. Lắp ráp các thành phần (Composition)
        super().__init__(
            provider_name="gemini",
            auth_strategy=ApiKeyHeader(api_key=str(config.api_key), header_name="x-goog-api-key"),
            endpoint_builder=EndpointBuilder(base_url=str(config.base_url)),
            api_mapper=ApiTypeMapper(api_map=GEMINI_API_MAP),
            model_mapper=ModelMapper(model_map=GEMINI_MODEL_MAP),
            capability_manager=ModelCapabilityManager(provider_name="gemini"), # Có thể tạo GeminiCapabilityManager riêng sau này
            provider_capabilities={
                ProviderCapability.BATCH_API, # Gemini hỗ trợ batch embeddings
                ProviderCapability.FINE_TUNING,
            }
        )
        self.config = config
        self.chat = GeminiChat(provider=self)
        self.files = GeminiFiles(provider=self)
        self.models = GeminiModels(provider=self)
        self.embeddings = GeminiEmbeddings(provider=self)

    def is_configured(self) -> bool:
        """Kiểm tra xem Gemini API key đã được cung cấp hay chưa."""
        return bool(self.config.api_key)


    async def image_generation(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError("Gemini image generation adapter is not implemented yet.")
    

    async def moderation(self, **kwargs) -> Any: raise NotImplementedError
    async def computer_use(self, **kwargs) -> Any: raise NotImplementedError
    async def provider_info(self, **kwargs) -> Any: raise NotImplementedError
    async def health(self, **kwargs) -> Any: raise NotImplementedError
    # ... and so on for all other abstract methods defined in BaseProvider.