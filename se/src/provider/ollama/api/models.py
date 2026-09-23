import asyncio
import asyncio
from typing import Dict, Any, Optional

import httpx

from ...core import BaseProvider, ApiType
from ...core.interfaces.model import ModelProvider
from ....domain.schemas import ModelInfo, ModelList
from ...exceptions import (
    ProviderModelUnavailableError,
    ResponseValidationError,
)
from ..converters.model.adapter import OllamaModelAdapter
from ..converters.model.capabilities import OllamaCapabilityResolver


class OllamaModels(ModelProvider):
    def __init__(self, provider: BaseProvider):
        self.provider = provider

    def _model_unavailable_error(
        self,
        model_id: str,
        *,
        status_code: int | None = None,
        raw_response: Any = None,
    ) -> ProviderModelUnavailableError:
        return ProviderModelUnavailableError(
            f"Model '{model_id}' is not available on Ollama.",
            provider_name=self.provider.name,
            status_code=status_code,
            raw_response=raw_response,
        )

    async def _fetch_show_data(
        self,
        model_id: str,
        http_client: Any,
        timeout: Any,
    ) -> Optional[Dict[str, Any]]:
        """Fetch /api/show while preserving model-missing vs transport failure."""
        try:
            response = await self.provider.send(
                client=http_client,
                method="POST",
                api_type=ApiType.MODEL,
                json={"model": model_id},
                timeout=timeout,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            try:
                raw_response = exc.response.json()
            except Exception:
                raw_response = exc.response.text
            raise self._model_unavailable_error(
                model_id,
                status_code=404,
                raw_response=raw_response,
            ) from exc

        try:
            data = response.json()
        except (TypeError, ValueError) as exc:
            raise ResponseValidationError(
                "Ollama /api/show returned invalid JSON.",
                provider_name=self.provider.name,
                status_code=getattr(response, "status_code", None),
                raw_response=getattr(response, "text", None),
            ) from exc

        show_payload_keys = {
            "details",
            "model_info",
            "template",
            "modelfile",
            "parameters",
            "capabilities",
            "system",
            "license",
            "messages",
        }
        if (
            not isinstance(data, dict)
            or not data
            or data.get("error")
            or not show_payload_keys.intersection(data)
        ):
            raise ResponseValidationError(
                "Ollama /api/show returned an invalid response shape.",
                provider_name=self.provider.name,
                status_code=getattr(response, "status_code", None),
                raw_response=data,
            )

        return data

    async def _resolve_full_capabilities(
        self, model_id: str, show_data: Optional[Dict[str, Any]], http_client: Any, timeout: Any
    ) -> set:
        """Hợp nhất capability từ CapabilityManager, /api/show payload và heuristic fallback."""
        # 1. Ưu tiên lấy từ CapabilityManager
        caps = await self.provider.capability_manager.get_capabilities_for_model(
            provider=self.provider,
            model_name=model_id,
            http_client=http_client,
            timeout=timeout,
        )
        if caps:
            return caps

        # 2. Phân tích từ response của /api/show nếu có
        if show_data:
            return OllamaCapabilityResolver.detect_from_show_response(show_data)

        # 3. Fallback phân tích theo Tên Model
        return OllamaCapabilityResolver.detect_from_name(model_id)

    async def model(self, model_id: str, **kwargs) -> ModelInfo:
        """Lấy thông tin chi tiết của một model đơn lẻ thông qua /api/show."""
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        # Gọi /api/show lấy chi tiết model
        show_data = await self._fetch_show_data(model_id, http_client, timeout)
        if not show_data:
            raise self._model_unavailable_error(model_id)

        capabilities = await self._resolve_full_capabilities(model_id, show_data, http_client, timeout)

        return OllamaModelAdapter.to_model_info(
            model_id=model_id,
            provider_name=self.provider.name,
            capabilities=capabilities,
            show_details=show_data,
        )

    async def models(self, **kwargs) -> ModelList:
        """Lấy danh sách các model từ Ollama qua /api/tags và enrich chi tiết qua /api/show."""
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        response = await self.provider.send(
            client=http_client,
            method="GET",
            api_type=ApiType.MODELS,
            timeout=timeout,
        )

        # httpx.Response.json() is synchronous. Awaiting it only fails after a
        # successful /api/tags response because the returned dict is not
        # awaitable.
        ollama_data = response.json()
        models_list = ollama_data.get("models", [])

        async def build_info(model_data: Dict[str, Any]) -> Optional[ModelInfo]:
            model_id = model_data.get("name")
            if not model_id:
                return None

            try:
                show_data = await self._fetch_show_data(
                    model_id,
                    http_client,
                    timeout,
                )
            except ProviderModelUnavailableError:
                # /api/tags can race with local model removal. A definitively
                # absent listed model is skipped, while transport failures
                # continue to propagate.
                return None

            capabilities = await self._resolve_full_capabilities(
                model_id,
                show_data,
                http_client,
                timeout,
            )

            return OllamaModelAdapter.to_model_info(
                model_id=model_id,
                provider_name=self.provider.name,
                capabilities=capabilities,
                show_details=show_data,
            )

        tasks = [build_info(m) for m in models_list]
        results = await asyncio.gather(*tasks)

        return ModelList(data=[info for info in results if info])

    async def model_capabilities(self, **kwargs) -> Any:
        """Trả về danh sách capability mặc định của Provider."""
        return self.provider.capability_manager.get_default_capabilities(self.provider.name)
