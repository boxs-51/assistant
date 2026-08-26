import httpx
from typing import Dict, Any
from .base import BaseExecutionHandler
from ...domain.schemas import ModelCapability
from ...infrastructure.config import settings
from ..exceptions import NoAvailableProviderError

class EmbeddingExecutionHandler(BaseExecutionHandler):
    """Xử lý tạo Vector Embeddings qua ProviderRuntime."""

    async def execute(self, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> Any:
        model = body.get("model")
        initial_chain = self.routing_policy.get_fallback_chain(model)
        if not initial_chain:
            raise NoAvailableProviderError(f"No provider configured for model '{model}'.")

        last_error = None
        for provider in await self._get_healthy_fallback_chain(initial_chain):
            try:
                if not await provider.has_capability(
                    model, ModelCapability.EMBEDDINGS, http_client, settings.provider.timeout
                ):
                    continue
                return await self.executor.execute_generic(
                    provider=provider,
                    execution_callable=lambda p=provider: p.embeddings.embeddings(
                        http_client=http_client, body=body, timeout=settings.provider.timeout
                    ),
                )
            except Exception as exc:
                last_error = exc
                continue
        raise NoAvailableProviderError("All embedding providers are unavailable or unsupported.") from last_error