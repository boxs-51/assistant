"""Deterministic offline provider used by Phase 0 tests and local E2E runs."""

from __future__ import annotations

import hashlib
import math
import os
import time
import uuid
from io import BytesIO
from typing import Any, AsyncGenerator, Dict, Optional

from ..core import (
    ApiTypeMapper,
    BaseProvider,
    EndpointBuilder,
    ModelCapabilityManager,
    ModelMapper,
    NoAuth,
)
from ...infrastructure.config import settings
from ...domain.schemas import (
    GatewayChoice,
    GatewayMessage,
    GatewayResponse,
    GatewayStreamChunk,
    GatewayStreamChoice,
    GatewayStreamDelta,
    GatewayUsage,
    ModelCapability,
    ModelInfo,
    ModelList,
    ContextLimits,
)

MOCK_MODEL = "mock-chat"
MOCK_EMBEDDING_MODEL = "mock-embedding"


class MockChat:
    def __init__(self, provider: "MockProvider"):
        self.provider = provider

    @staticmethod
    def _last_user_text(body: Dict[str, Any]) -> str:
        messages = body.get("messages") or []
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content", "")
                if isinstance(content, str):
                    return content
                return str(content)
        return ""

    async def chat(self, **kwargs) -> GatewayResponse:
        body = kwargs.get("body") or {}
        model = body.get("model") or MOCK_MODEL
        text = self._last_user_text(body)
        answer = f"mock:{text}" if text else "mock:ok"
        prompt_tokens = max(1, len(text.split())) if text else 1
        completion_tokens = max(1, len(answer.split()))
        return GatewayResponse(
           id=f"mock-{uuid.uuid4().hex}",
           model=model,
            provider=self.provider.name,
           choices=[GatewayChoice(
               index=0,
                message=GatewayMessage(role="assistant", content=answer),
                finish_reason="stop",
            )],
            usage=GatewayUsage(
               prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            metadata={"mock": True},
        )

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        body = kwargs.get("body") or {}
        model = body.get("model") or MOCK_MODEL
        text = self._last_user_text(body)
        answer = f"mock:{text}" if text else "mock:ok"
        words = answer.split(" ")
        for index, word in enumerate(words):
            yield GatewayStreamChunk(
                id=f"mock-stream-{uuid.uuid4().hex}",
                model=model,
                provider=self.provider.name,
                choices=[GatewayStreamChoice(
                    index=0,
                    delta=GatewayStreamDelta(
                        content=(word + (" " if index < len(words) - 1 else "")),
                        role="assistant" if index == 0 else None,
                    ),
                    finish_reason="stop" if index == len(words) - 1 else None,
                )],
                metadata={"mock": True},
            )


class MockModels:
    def __init__(self, provider: "MockProvider"):
        self.provider = provider

    def _model(self, model_id: str, embedding: bool = False) -> ModelInfo:
        caps = {ModelCapability.EMBEDDINGS, ModelCapability.EMBEDDINGS_BATCH} if embedding else {
            ModelCapability.CHAT,
            ModelCapability.CHAT_STREAM,
            ModelCapability.TOKEN_COUNT,
            ModelCapability.JSON_MODE,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.TOOL_CALLING,
        }
        return ModelInfo(
            id=model_id,
            display_name=model_id,
            provider=self.provider.name,
            family="mock",
            version="1.0",
            description="Deterministic offline Phase 0 mock model",
            limits=ContextLimits(context_window=32768, max_output_tokens=4096),
            capabilities=caps,
            owned_by="phase0",
            metadata={"mock": True},
        )

    async def models(self, **kwargs) -> ModelList:
        return ModelList(data=[self._model(MOCK_MODEL), self._model(MOCK_EMBEDDING_MODEL, embedding=True)])

    async def model(self, **kwargs) -> ModelInfo:
        model_id = kwargs.get("model_name") or MOCK_MODEL
        return self._model(model_id, embedding="embedding" in model_id)


class MockEmbeddings:
    def __init__(self, provider: "MockProvider"):
        self.provider = provider

    @staticmethod
    def _vector(text: str, dimensions: int = 8) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [int.from_bytes(digest[i:i + 4], "big") / 2**32 for i in range(0, dimensions * 4, 4)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]

    async def embeddings(self, **kwargs) -> Dict[str, Any]:
        body = kwargs.get("body") or {}
        model = body.get("model") or MOCK_EMBEDDING_MODEL
        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        return {
            "object": "list",
            "model": model,
            "data": [
                {"object": "embedding", "index": i, "embedding": self._vector(str(value))}
                for i, value in enumerate(inputs)
            ],
            "usage": {"prompt_tokens": sum(max(1, len(str(x).split())) for x in inputs), "total_tokens": sum(max(1, len(str(x).split())) for x in inputs)},
        }


class MockFiles:
    def __init__(self, provider: "MockProvider"):
        self.provider = provider
        self._files: Dict[str, Dict[str, Any]] = {}

    async def list_files(self, **kwargs) -> Dict[str, Any]:
        return {"object": "list", "data": list(self._files.values()), "next_page_token": None}

    async def upload_file(self, **kwargs) -> Dict[str, Any]:
        stream = kwargs["file_stream"]
        content = stream.read()
        file_id = f"mock-file-{uuid.uuid4().hex}"
        entry = {
            "name": file_id,
            "display_name": kwargs.get("display_name") or file_id,
            "mime_type": kwargs.get("mime_type") or "application/octet-stream",
            "size": len(content),
            "bytes": content,
            "created": int(time.time()),
        }
        self._files[file_id] = entry
        return {k: v for k, v in entry.items() if k != "bytes"}

    async def get_file(self, **kwargs) -> Dict[str, Any]:
        file_id = kwargs["file_id"]
        entry = self._files[file_id]
        return {k: v for k, v in entry.items() if k != "bytes"}

    async def download_file(self, **kwargs) -> bytes:
        return self._files[kwargs["file_id"]]["bytes"]

    async def delete_file(self, **kwargs) -> bool:
        return self._files.pop(kwargs["file_id"], None) is not None


class MockProvider(BaseProvider):
    """Provider with zero network I/O and deterministic responses."""

    def __init__(self):
        super().__init__(
            provider_name="mock",
            auth_strategy=NoAuth(),
            endpoint_builder=EndpointBuilder(base_url="http://mock.invalid"),
            api_mapper=ApiTypeMapper(api_map={}),
            model_mapper=ModelMapper(model_map={
                MOCK_MODEL: MOCK_MODEL,
                MOCK_EMBEDDING_MODEL: MOCK_EMBEDDING_MODEL,
            }),
            capability_manager=ModelCapabilityManager(provider_name="mock"),
        )
        self.chat = MockChat(self)
        self.models = MockModels(self)
        self.embeddings = MockEmbeddings(self)
        self.files = MockFiles(self)

    @classmethod
    def is_configured(cls) -> bool:
        env_enabled = os.getenv("GATEWAY_PROVIDER__MOCK_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        try:
            configured = bool(settings.provider.mock_enabled)
        except RuntimeError:
            configured = False
        return env_enabled or configured

    async def has_capability(self, model_name: str, capability: ModelCapability, http_client, timeout: float) -> bool:
        if model_name == MOCK_EMBEDDING_MODEL:
            return capability in {ModelCapability.EMBEDDINGS, ModelCapability.EMBEDDINGS_BATCH}
        if model_name == MOCK_MODEL:
            return capability in {
                ModelCapability.CHAT,
                ModelCapability.CHAT_STREAM,
                ModelCapability.TOKEN_COUNT,
                ModelCapability.JSON_MODE,
                ModelCapability.STRUCTURED_OUTPUT,
                ModelCapability.TOOL_CALLING,
            }
        return False

    async def models_info(self, **kwargs):
        return await self.models.models(**kwargs)

    async def moderation(self, **kwargs):
        raise NotImplementedError("Mock provider does not implement moderation")

    async def computer_use(self, **kwargs):
        raise NotImplementedError("Mock provider does not implement computer_use")

    async def provider_info(self, **kwargs):
        return {"name": self.name, "version": "1.0", "mock": True}

    async def health(self, **kwargs):
        return {"status": "ok", "provider": self.name, "mock": True}