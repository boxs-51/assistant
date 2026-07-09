from typing import Dict, Any
import asyncio

from ...core import BaseProvider, ApiType
from ...core.interfaces.model import ModelProvider
from .....schemas import ModelInfo, ModelList

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
    
    async def model_capabilities(self, **kwargs) -> Any: raise NotImplementedError