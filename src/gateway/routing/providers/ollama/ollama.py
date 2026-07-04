import httpx
import asyncio
from typing import Dict, Any, AsyncGenerator

from ..base.provider import BaseProvider
from ..base.api import ApiType
from ..base.auth import NoAuth
from ..base.api_mapper import ApiTypeMapper
from ..base.endpoint import EndpointBuilder
from ..base.model_capability import DefaultModelCapabilityManager
from ..base.model_mapper import ModelMapper
from .adapter import OllamaAdapter

from ....config import settings
from ....schemas import GatewayResponse, GatewayStreamChunk, ModelList, ModelInfo

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
            adapter=OllamaAdapter(),
            api_mapper=ApiTypeMapper(api_map=OLLAMA_API_MAP),
            model_mapper=ModelMapper(model_map=OLLAMA_MODEL_MAP),
            capability_manager=DefaultModelCapabilityManager(provider_name="ollama")
        )
        self.DEFAULT_MODEL = "llama3"

    @classmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem Ollama base URL đã được cung cấp hay chưa."""
        return bool(settings.ollama.base_url)

    async def chat(self, **kwargs) -> GatewayResponse:
        prepared_body = self.prepare_request(kwargs.get("body"))
        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout")
        )
        return await self.adapter.adapt_chat_response(response)

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        prepared_body = self.prepare_request(kwargs.get("body"))
        response = await self.send(
            client=kwargs.get("http_client"),
            api_type=ApiType.CHAT_COMPLETIONS,
            json=prepared_body,
            timeout=kwargs.get("timeout")
        )
        
        async for chunk in self.adapter.adapt_chat_stream(response):
            yield chunk

    # =======================================================================
    # Placeholder implementations for abstract methods from BaseProvider
    # =======================================================================

    async def chat_batch(self, **kwargs): raise NotImplementedError
    
    async def models(self, **kwargs) -> ModelList:
        """Lấy danh sách các model đã được pull về từ Ollama."""
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        response = await self.send(
            client=http_client,
            method="GET",
            api_type=ApiType.MODELS,
            timeout=timeout,
        )

        ollama_data = await response.json()
        models_list = ollama_data.get("models", [])

        async def get_model_info(model_data: Dict[str, Any]) -> ModelInfo | None:
            model_id = model_data.get("name")
            if not model_id:
                return None
            
            capabilities_set = await self.capability_manager.get_capabilities_for_model(
                provider=self,
                model_name=model_id,
                http_client=http_client,
                timeout=timeout
            )
            
            return ModelInfo(id=model_id, owned_by="ollama", capabilities=[cap.name for cap in capabilities_set])

        tasks = [get_model_info(m) for m in models_list]
        results = await asyncio.gather(*tasks)
        
        return ModelList(data=[info for info in results if info])

    async def model(self, **kwargs): 
        """Lấy thông tin chi tiết một model. Ollama API không hỗ trợ trực tiếp, cần gọi /api/show."""
        raise NotImplementedError("Ollama does not support fetching single model details via a dedicated endpoint in this manner.")
    async def model_capabilities(self, **kwargs): raise NotImplementedError
    async def embeddings_batch(self, **kwargs): raise NotImplementedError
    async def image_edit(self, **kwargs): raise NotImplementedError
    async def image_variation(self, **kwargs): raise NotImplementedError
    async def speech_to_text(self, **kwargs) -> Any: raise NotImplementedError
    async def speech_to_text_stream(self, **kwargs) -> Any: raise NotImplementedError
    async def text_to_speech(self, **kwargs) -> Any: raise NotImplementedError
    async def text_to_speech_stream(self, **kwargs) -> Any: raise NotImplementedError
    async def audio_translation(self, **kwargs) -> Any: raise NotImplementedError
    async def video_generation(self, **kwargs) -> Any: raise NotImplementedError
    async def video_understanding(self, **kwargs) -> Any: raise NotImplementedError
    async def upload_file(self, **kwargs) -> Any: raise NotImplementedError
    async def download_file(self, **kwargs) -> Any: raise NotImplementedError
    async def delete_file(self, **kwargs) -> Any: raise NotImplementedError
    async def list_files(self, **kwargs) -> Any: raise NotImplementedError
    async def get_file(self, **kwargs) -> Any: raise NotImplementedError
    async def create_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def list_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def get_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def update_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def delete_cache(self, **kwargs) -> Any: raise NotImplementedError
    async def count_tokens(self, **kwargs) -> Any: raise NotImplementedError
    async def tokenize(self, **kwargs) -> Any: raise NotImplementedError
    async def detokenize(self, **kwargs) -> Any: raise NotImplementedError
    async def tool_call(self, **kwargs) -> Any: raise NotImplementedError
    async def execute_tool(self, **kwargs) -> Any: raise NotImplementedError
    async def web_search(self, **kwargs) -> Any: raise NotImplementedError
    async def url_context(self, **kwargs) -> Any: raise NotImplementedError
    async def execute_code(self, **kwargs) -> Any: raise NotImplementedError
    async def live(self, **kwargs) -> Any: raise NotImplementedError
    async def live_stream(self, **kwargs) -> Any: raise NotImplementedError
    async def create_session(self, **kwargs) -> Any: raise NotImplementedError
    async def delete_session(self, **kwargs) -> Any: raise NotImplementedError
    async def get_session(self, **kwargs) -> Any: raise NotImplementedError
    async def list_sessions(self, **kwargs) -> Any: raise NotImplementedError
    async def create_batch(self, **kwargs) -> Any: raise NotImplementedError
    async def batch_status(self, **kwargs) -> Any: raise NotImplementedError
    async def cancel_batch(self, **kwargs) -> Any: raise NotImplementedError
    async def list_batches(self, **kwargs) -> Any: raise NotImplementedError
    async def fine_tune(self, **kwargs) -> Any: raise NotImplementedError
    async def list_fine_tunes(self, **kwargs) -> Any: raise NotImplementedError
    async def fine_tune_status(self, **kwargs) -> Any: raise NotImplementedError
    async def cancel_fine_tune(self, **kwargs) -> Any: raise NotImplementedError
    async def assistant(self, **kwargs) -> Any: raise NotImplementedError
    async def assistant_stream(self, **kwargs) -> Any: raise NotImplementedError
    async def moderation(self, **kwargs) -> Any: raise NotImplementedError
    async def rerank(self, **kwargs) -> Any: raise NotImplementedError
    async def vision(self, **kwargs) -> Any: raise NotImplementedError
    async def ocr(self, **kwargs) -> Any: raise NotImplementedError
    async def provider_info(self, **kwargs) -> Any: raise NotImplementedError
    async def embeddings(self, **kwargs) -> Any: raise NotImplementedError
    async def image_generation(self, **kwargs) -> Any: raise NotImplementedError
    async def health(self, **kwargs) -> Any: raise NotImplementedError
    # For brevity, only a few are shown here, but all would be added.
    async def computer_use(self, **kwargs): raise NotImplementedError
    async def browser(self, **kwargs): raise NotImplementedError