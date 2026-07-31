from typing import Dict, Any, AsyncGenerator

from ..core import (
    BaseProvider, ApiType,
    NoAuth, ApiTypeMapper,
    EndpointBuilder,
    ModelCapabilityManager, ModelMapper
)

from ...infrastructure.config import settings
from .api.chats import OllamaChats
from .api.models import OllamaModels

# Ollama không cần mapping phức tạp, nhưng vẫn giữ cấu trúc để nhất quán
OLLAMA_MODEL_MAP = {}

# Ánh xạ ApiType sang endpoint template của Ollama
OLLAMA_API_MAP = {
    ApiType.CHAT_COMPLETIONS: "api/chat",
    ApiType.MODELS: "api/tags", # Endpoint để lấy danh sách model
}

class OllamaProvider(BaseProvider):
    """Nhà cung cấp cho các mô hình local qua Ollama."""
    def __init__(self):
        super().__init__(
            provider_name="ollama",
            auth_strategy=NoAuth(),
            endpoint_builder=EndpointBuilder(base_url=str(settings.ollama.base_url)),
            api_mapper=ApiTypeMapper(api_map=OLLAMA_API_MAP),
            model_mapper=ModelMapper(model_map=OLLAMA_MODEL_MAP),
            capability_manager=ModelCapabilityManager(provider_name="ollama")
        )
        self.chat = OllamaChats(provider=self)
        self.models = OllamaModels(provider=self)

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Ollama base URL đã được cung cấp hay chưa."""
        return bool(settings.ollama.base_url)

    async def moderation(self, **kwargs) -> Any: raise NotImplementedError
    async def provider_info(self, **kwargs) -> Any: raise NotImplementedError
    async def health(self, **kwargs) -> Any: raise NotImplementedError
    async def computer_use(self, **kwargs): raise NotImplementedError
