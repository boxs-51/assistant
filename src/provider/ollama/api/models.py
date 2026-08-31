from typing import Dict, Any
import asyncio

from ...core import BaseProvider, ApiType
from ...core.interfaces.model import ModelProvider
from ....domain.schemas import ModelInfo, ModelList, ContextLimits

class OllamaModels(ModelProvider):
    def __init__(self, provider: BaseProvider):
        self.provider = provider

    async def models(self, **kwargs) -> ModelList:
        """Lấy danh sách các model đã được pull về từ Ollama."""
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        response = await self.provider.send(
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
            
            capabilities_set = await self.provider.capability_manager.get_capabilities_for_model(
                provider=self.provider,
                model_name=model_id,
                http_client=http_client,
                timeout=timeout
            )
            
            return ModelInfo(
                id=model_id,
                display_name=model_id,
                provider=self.provider.name,
                family=model_id.split(":")[0] if ":" in model_id else model_id.split("-")[0],
                version="v1",
                description="",
                limits=ContextLimits(context_window=32768, max_output_tokens=4096),
                capabilities=capabilities_set,
                owned_by="ollama",
            )

        tasks = [get_model_info(m) for m in models_list]
        results = await asyncio.gather(*tasks)
        
        return ModelList(data=[info for info in results if info])

    async def model(self, **kwargs): 
        """Lấy thông tin chi tiết một model. Ollama API không hỗ trợ trực tiếp, cần gọi /api/show."""
        raise NotImplementedError("Ollama does not support fetching single model details via a dedicated endpoint in this manner.")
    
    async def model_capabilities(self, **kwargs) -> Any: raise NotImplementedError