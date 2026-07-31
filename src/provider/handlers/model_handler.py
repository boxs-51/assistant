import httpx
from typing import Dict, Any, Optional
from .base import BaseExecutionHandler
from ...config import settings

class ModelOperationHandler(BaseExecutionHandler):
    """Xử lý tra cứu danh sách hoặc thông tin chi tiết Model từ các Provider."""

    async def execute(
        self, provider_name: str, model_id: Optional[str], http_client: httpx.AsyncClient
    ) -> Any:
        provider = self.providers.get(provider_name)
        if not provider:
            raise KeyError(f"Provider '{provider_name}' not found.")

        if model_id:
            model_data = await provider.models.model(
                http_client=http_client,
                timeout=settings.provider.timeout,
                model_name=model_id
            )
            return provider.capability_manager.enrich_capabilities(model_data)

        models_data = await provider.models.models(
            http_client=http_client,
            timeout=settings.provider.timeout
        )
        return provider.capability_manager.enrich_capabilities(models_data)