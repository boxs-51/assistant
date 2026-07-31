import asyncio
from typing import Dict, Any, AsyncGenerator

from ..core import (
    BaseProvider, ApiType,
    BearerToken, ApiTypeMapper,
    EndpointBuilder, 
    ModelCapabilityManager, ProviderCapability,
    ModelMapper
)

from ...infrastructure.config import settings
from ...domain.schemas import GatewayResponse, GatewayStreamChunk, ModelList, ModelInfo

from .api.chats import OpenAIChats

# Mapping model cho OpenAI, có thể mở rộng cho các model fallback
OPENAI_MODEL_MAP = {
    "gpt-4o": "gpt-4o",
    "gpt-4-turbo": "gpt-4-turbo",
    "gpt-3.5-turbo": "gpt-3.5-turbo",
    # Fallback mapping
    "gemini-1.5-pro": "gpt-4o",
}

# Ánh xạ ApiType sang endpoint template của OpenAI
OPENAI_API_MAP = {
    ApiType.CHAT_COMPLETIONS: "chat/completions",
    ApiType.MODELS: "models",
    ApiType.EMBEDDINGS: "embeddings",
}

class OpenAIProvider(BaseProvider):
    """Nhà cung cấp cho các mô hình của OpenAI hoặc các API tương thích OpenAI."""
    def __init__(self):
        super().__init__(
            provider_name="openai",
            auth_strategy=BearerToken(api_key=str(settings.openai.api_key)),
            endpoint_builder=EndpointBuilder(base_url=str(settings.openai.base_url)),
            api_mapper=ApiTypeMapper(api_map=OPENAI_API_MAP),
            model_mapper=ModelMapper(model_map=OPENAI_MODEL_MAP),
            capability_manager=ModelCapabilityManager(provider_name="openai"),
            provider_capabilities={
                ProviderCapability.FILES,
                ProviderCapability.ASSISTANTS,
                ProviderCapability.FINE_TUNING,
            }
        )
        self.chat = OpenAIChats(provider=self)

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem OpenAI API key đã được cung cấp hay chưa."""
        return bool(settings.openai.api_key)



    async def models(self, **kwargs) -> ModelList:
        """Lấy danh sách các model từ OpenAI."""
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        response = await self.send(
            client=http_client,
            method="GET",
            api_type=ApiType.MODELS,
            timeout=timeout,
        )
        
        openai_data = response.json()
        models_list = openai_data.get("data", [])

        async def get_model_info(model_data: Dict[str, Any]) -> ModelInfo | None:
            model_id = model_data.get("id")
            if not model_id:
                return None
            
            capabilities_set = await self.capability_manager.get_capabilities_for_model(
                provider=self, model_name=model_id, http_client=http_client, timeout=timeout
            )
            
            return ModelInfo(id=model_id, owned_by=model_data.get("owned_by", "openai"), capabilities=[cap.name for cap in capabilities_set])

        tasks = [get_model_info(m) for m in models_list]
        results = await asyncio.gather(*tasks)
        
        return ModelList(data=[info for info in results if info])

    
    async def model(self, **kwargs) -> Dict[str, Any]:
        """Lấy thông tin chi tiết của một model cụ thể từ OpenAI."""
        model_name = kwargs.get("model_name")
        # OpenAI API dùng endpoint /v1/models/{model_id}
        api_type_template = f"models/{model_name}"
        response = await self.send(client=kwargs.get("http_client"), method="GET", api_type=api_type_template, timeout=kwargs.get("timeout"))
        return await response.json()


    async def embeddings(self, **kwargs) -> Dict[str, Any]:
        """Tạo embeddings cho văn bản bằng API của OpenAI."""
        prepared_body = self.prepare_request(kwargs.get("body"))
        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.EMBEDDINGS,
            json=prepared_body,
            timeout=kwargs.get("timeout"),
        )
        return await response.json()