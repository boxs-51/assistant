"""Deterministic, zero-network provider for offline unit/runtime/API tests."""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import time
from typing import Any, AsyncGenerator, Dict
from io import BytesIO
import structlog
from ..core import ApiTypeMapper, BaseProvider, EndpointBuilder, ModelCapabilityManager, ModelMapper, NoAuth
from ..exceptions import ProviderError
from .errors import build_mock_error
from .scenarios import MockScenario
from .state import MockState
from ...domain.schemas import (
    ContextLimits, GatewayChoice, GatewayMessage, GatewayResponse,
    GatewayStreamChoice, GatewayStreamChunk, GatewayStreamDelta, GatewayUsage,
    ModelCapability, ModelInfo, ModelList,
)

from ...infrastructure.config.schemas import ProviderConfig

logger = structlog.get_logger(__name__)

MOCK_CHAT_MODEL = "mock-chat"
MOCK_EMBEDDING_MODEL = "mock-embedding"
MOCK_VISION_MODEL = "mock-vision"
MOCK_AUDIO_MODEL = "mock-audio"
MOCK_IMAGE_MODEL = "mock-image"
MOCK_VIDEO_MODEL = "mock-video"
MOCK_TOOL_MODEL = "mock-tool"
MOCK_BATCH_MODEL = "mock-batch"
MOCK_RERANK_MODEL = "mock-rerank"
MOCK_REASONING_MODEL = "mock-reasoning"
MOCK_MODEL = MOCK_CHAT_MODEL

MODEL_CAPABILITIES: dict[str, set[ModelCapability]] = {
    MOCK_CHAT_MODEL: {
        ModelCapability.CHAT, ModelCapability.CHAT_STREAM, ModelCapability.CHAT_BATCH,
        ModelCapability.TOKEN_COUNT, ModelCapability.TOKENIZE,
        ModelCapability.TOOL_CALLING, ModelCapability.WEB_SEARCH,
        ModelCapability.CODE_EXECUTION, ModelCapability.JSON_MODE,
        ModelCapability.STRUCTURED_OUTPUT,
    },
    MOCK_EMBEDDING_MODEL: {
        ModelCapability.EMBEDDINGS, ModelCapability.EMBEDDINGS_BATCH,
        ModelCapability.TOKEN_COUNT, ModelCapability.TOKENIZE,
    },
    MOCK_VISION_MODEL: {
        ModelCapability.CHAT, ModelCapability.CHAT_STREAM,
        ModelCapability.VISION, ModelCapability.OCR,
    },
    MOCK_AUDIO_MODEL: {
        ModelCapability.SPEECH_TO_TEXT, ModelCapability.SPEECH_TO_TEXT_STREAM,
        ModelCapability.TEXT_TO_SPEECH, ModelCapability.TEXT_TO_SPEECH_STREAM,
        ModelCapability.AUDIO_TRANSLATION,
    },
    MOCK_IMAGE_MODEL: {
        ModelCapability.IMAGE_GENERATION, ModelCapability.IMAGE_EDIT, ModelCapability.IMAGE_VARIATION,
    },
    MOCK_VIDEO_MODEL: {ModelCapability.VIDEO_GENERATION, ModelCapability.VIDEO_UNDERSTANDING},
    MOCK_TOOL_MODEL: {ModelCapability.TOOL_CALLING, ModelCapability.WEB_SEARCH, ModelCapability.CODE_EXECUTION},
    MOCK_BATCH_MODEL: {ModelCapability.CHAT_BATCH, ModelCapability.EMBEDDINGS_BATCH},
    MOCK_RERANK_MODEL: {ModelCapability.RERANK},
    MOCK_REASONING_MODEL: {ModelCapability.CHAT, ModelCapability.CHAT_STREAM, ModelCapability.TOKEN_COUNT, ModelCapability.JSON_MODE, ModelCapability.STRUCTURED_OUTPUT},
}


class MockChat:
    def __init__(self, provider):
        self.provider = provider

    @staticmethod
    def _text(body: Dict[str, Any]) -> str:
        messages = body.get("messages") or []
        for message in reversed(messages):
            if message.get("role") == "user":
                value = message.get("content", "")
                if isinstance(value, str):
                    return value
                if isinstance(value, list):
                    parts = []
                    for item in value:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                    return " ".join(parts)
                return str(value)
        return ""

    def _build_chat_answer(self, text: str) -> str:
        if self.provider.scenario.fixed_chat_response is not None:
            return self.provider.scenario.fixed_chat_response
        if text:
            return f"mock:{text}"
        return "mock:ok"

    def _build_stream_answer(self, text: str) -> str:
        if self.provider.scenario.fixed_chat_response is not None:
            return self.provider.scenario.fixed_chat_response
        if text:
            return " ".join(f"mock:{word}" for word in text.split())
        return "mock:ok"

    async def chat(self, **kwargs) -> GatewayResponse:
        self.provider._before("chat")
        body = kwargs.get("body") or {}
        model = body.get("model") or MOCK_CHAT_MODEL
        text = self._text(body)
        answer = self._build_chat_answer(text)

        prompt_tokens = max(1, len(text.split())) if text else 1
        completion_tokens = max(1, len(answer.split())) if answer else 1

        return GatewayResponse(
            id=self.provider.state.stable_id("chat", self.provider.request_key(body)),
            model=model,
            provider=self.provider.name,
            choices=[
                GatewayChoice(
                    index=0,
                    message=GatewayMessage(role="assistant", content=answer),
                    finish_reason="stop"
                )
            ],
            usage=GatewayUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens
            ),
            metadata={"mock": True, "scenario": self.provider.scenario.name},
        )

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        self.provider._before("chat_stream")
        self.provider._check_stream_fault(operation="chat_stream", phase="pre_stream")

        body = kwargs.get("body") or {}
        model = body.get("model") or MOCK_CHAT_MODEL
        text = self._text(body)
        answer = self._build_stream_answer(text)

        words = answer.split() if answer else [""]
        size = max(1, self.provider.scenario.stream_chunk_size)
        chunk_number = 0
        request_key = self.provider.request_key(body)
        total_words = len(words)

        for start in range(0, total_words, size):
            if (
                self.provider.scenario.fail_after_chunks is not None
                and chunk_number >= self.provider.scenario.fail_after_chunks
            ):
                self.provider._check_stream_fault(
                    operation="chat_stream",
                    phase="mid_stream",
                    chunk_number=chunk_number
                )

            is_last_chunk = (start + size) >= total_words
            part = " ".join(words[start:start + size])
            if not is_last_chunk:
                part += " "

            if self.provider.scenario.latency_ms:
                await asyncio.sleep(self.provider.scenario.latency_ms / 1000.0)

            yield GatewayStreamChunk(
                id=self.provider.state.stable_id("stream", request_key),
                model=model,
                provider=self.provider.name,
                choices=[
                    GatewayStreamChoice(
                        index=0,
                        delta=GatewayStreamDelta(
                            content=part,
                            role="assistant" if start == 0 else None
                        ),
                        finish_reason="stop" if is_last_chunk else None
                    )
                ],
                metadata={"mock": True, "scenario": self.provider.scenario.name},
            )
            chunk_number += 1

        self.provider._check_stream_fault(operation="chat_stream", phase="post_stream")


class MockModels:
    def __init__(self, provider): self.provider = provider
    def _model(self, model_id):
        return ModelInfo(
            id=model_id, display_name=model_id, provider=self.provider.name,
            family="mock", version="1.0", description="Deterministic zero-network mock model",
            limits=ContextLimits(context_window=32768, max_output_tokens=4096),
            capabilities=MODEL_CAPABILITIES[model_id], owned_by="assistant-tests", metadata={"mock": True}, created=0,
        )
    async def models(self, **kwargs):
        self.provider._before("models")
        return ModelList(data=[self._model(mid) for mid in MODEL_CAPABILITIES])
    async def model(self, **kwargs):
        self.provider._before("model")
        model_id = kwargs.get("model_name") or MOCK_CHAT_MODEL
        if model_id not in MODEL_CAPABILITIES: raise KeyError(f"Model '{model_id}' not found in mock provider.")
        return self._model(model_id)


class MockEmbeddings:
    def __init__(self, provider): self.provider = provider
    @staticmethod
    def _vector(text: str, dimensions: int = 8):
        digest = hashlib.sha256(text.encode()).digest()
        raw = [int.from_bytes(digest[i:i+4], "big") / 2**32 for i in range(0, dimensions * 4, 4)]
        norm = math.sqrt(sum(v*v for v in raw)) or 1.0
        return [v / norm for v in raw]
    async def embeddings(self, **kwargs):
        self.provider._before("embeddings")
        body = kwargs.get("body") or {}
        inputs = body.get("input", [])
        if isinstance(inputs, str): inputs = [inputs]
        tokens = sum(max(1, len(str(x).split())) for x in inputs)
        return {"object":"list", "model":body.get("model") or MOCK_EMBEDDING_MODEL,
                "data":[{"object":"embedding","index":i,"embedding":self._vector(str(x))} for i,x in enumerate(inputs)],
                "usage":{"prompt_tokens":tokens,"total_tokens":tokens}}


class MockFiles:
    def __init__(self, provider): 
        self.provider = provider

    async def list_files(self, **kwargs):
        self.provider._before("files.list")
        return {
            "object": "list", 
            "data": [{k: v for k, v in x.items() if k != "bytes"} for x in self.provider.state.files.values()], 
            "next_page_token": None
        }

    async def upload_file(self, **kwargs):
        self.provider._before("files.upload")
        stream = kwargs["file_stream"]
        content = stream.read()
        display = kwargs.get("display_name") or "file"
        file_id = self.provider.state.stable_id("file", display + ":" + hashlib.sha1(content).hexdigest())
        
        file_data = {
            "name": file_id,
            "display_name": display,
            "filename": display,
            "mime_type": kwargs.get("mime_type") or "application/octet-stream",
            "size": len(content),
            "bytes": content,
            "created": 0,
            "uri": f"mock://files/{file_id}"
        }
        self.provider.state.files[file_id] = file_data
        return {k: v for k, v in file_data.items() if k != "bytes"}

    async def get_file(self, **kwargs):
        self.provider._before("files.get")
        file_id = kwargs.get("file_id") or kwargs.get("file_name")
        file_data = self.provider.state.files.get(file_id)
        if not file_data:
            raise KeyError(f"File '{file_id}' not found.")
        return {k: v for k, v in file_data.items() if k != "bytes"}

    async def download_file(self, **kwargs):
        self.provider._before("files.download")
        file_id = kwargs.get("file_id") or kwargs.get("file_name")
        uri = kwargs.get("uri")

        if not file_id and uri:
            file_id = uri.split("/")[-1]

        if file_id in self.provider.state.files:
            return self.provider.state.files[file_id]["bytes"]
        raise KeyError(f"File '{file_id}' not found for download.")

    async def delete_file(self, **kwargs):
        self.provider._before("files.delete")
        file_id = kwargs.get("file_id") or kwargs.get("file_name")
        return self.provider.state.files.pop(file_id, None) is not None


class MockAudio:
    def __init__(self, provider): self.provider = provider
    async def speech_to_text(self, **kwargs): self.provider._before("audio.stt"); return {"text":"mock transcription","language":kwargs.get("language","en"),"mock":True}
    async def text_to_speech(self, **kwargs): self.provider._before("audio.tts"); return {"audio":f"MOCK-AUDIO:{kwargs.get('text','')}".encode(),"format":kwargs.get("format","wav"),"mock":True}
    async def audio_translation(self, **kwargs): self.provider._before("audio.translation"); return {"text":"mock translated audio","mock":True}

class MockVision:
    def __init__(self, provider): self.provider = provider
    async def vision(self, **kwargs): self.provider._before("vision"); return {"labels":["mock-object"],"text":"mock OCR text","confidence":0.99,"mock":True}

class MockImage:
    def __init__(self, provider): self.provider = provider
    async def image_generation(self, **kwargs): self.provider._before("image.generate"); return {"created":0,"data":[{"b64_json":"bW9jay1pbWFnZQ=="}],"mock":True}
    async def image_edit(self, **kwargs): self.provider._before("image.edit"); return {"created":0,"data":[{"b64_json":"bW9jay1pbWFnZS1lZGl0"}],"mock":True}
    async def image_variation(self, **kwargs): self.provider._before("image.variation"); return {"created":0,"data":[{"b64_json":"bW9jay1pbWFnZS12YXJpYXRpb24="}],"mock":True}

class MockVideo:
    def __init__(self, provider): self.provider = provider
    async def video_generation(self, **kwargs):
        self.provider._before("video.generate"); jid=self.provider.state.stable_id("video",self.provider.request_key(kwargs)); self.provider.state.jobs[jid]={"id":jid,"status":"completed","mock":True}; return dict(self.provider.state.jobs[jid])
    async def video_understanding(self, **kwargs): self.provider._before("video.understand"); return {"labels":["mock-video"],"summary":"mock video understanding","mock":True}

class MockBatch:
    def __init__(self, provider): self.provider = provider
    async def create_batch(self, **kwargs):
        self.provider._before("batch.create"); body=kwargs.get("body") or kwargs; bid=self.provider.state.stable_id("batch",self.provider.request_key(body)); self.provider.state.batches[bid]={"id":bid,"status":"completed","mock":True}; return dict(self.provider.state.batches[bid])
    async def batch_status(self, **kwargs): self.provider._before("batch.status"); return dict(self.provider.state.batches[kwargs["batch_id"]])

class MockTokens:
    def __init__(self, provider): self.provider = provider
    async def count_tokens(self, **kwargs): self.provider._before("tokens.count"); body=kwargs.get("body") or kwargs; text=str(body.get("text") or body.get("input") or ""); return {"total_tokens":len(text.split()),"tokens":len(text.split()),"mock":True}

class MockReranking:
    def __init__(self, provider): self.provider = provider
    async def rerank(self, **kwargs):
        self.provider._before("rerank"); docs=list(kwargs.get("documents") or []); return {"results":[{"index":i,"relevance_score":round(1-i/max(1,len(docs)),6)} for i,_ in enumerate(docs)],"mock":True}

class MockTooling:
    def __init__(self, provider): self.provider = provider
    async def web_search(self, **kwargs): self.provider._before("web_search"); return {"query":kwargs.get("query",""),"results":[],"mock":True}
    async def code_execution(self, **kwargs): self.provider._before("code_execution"); return {"stdout":"mock code execution","stderr":"","exit_code":0,"mock":True}


class MockProvider(BaseProvider):
    """Provider that never performs outbound network I/O."""
    def __init__(self, *, config: ProviderConfig, scenario: MockScenario | None = None):

        super().__init__(
            provider_name="mock", 
            auth_strategy=NoAuth(), 
            endpoint_builder=EndpointBuilder(base_url=config.base_url), 
            api_mapper=ApiTypeMapper(api_map={}), 
            model_mapper=ModelMapper(model_map={m:m for m in MODEL_CAPABILITIES}), 
            capability_manager=ModelCapabilityManager(provider_name="mock"))
        
        self.config = config
        seed = config.options.get("seed", "assistant-offline-mock")
        self.state=MockState(seed); self.scenario=(scenario or MockScenario()).clone()
        self.chat=MockChat(self); self.models=MockModels(self); self.embeddings=MockEmbeddings(self); self.files=MockFiles(self)
        self.audio=MockAudio(self); self.vision=MockVision(self); self.image=MockImage(self); self.video=MockVideo(self)
        self.batch=MockBatch(self); self.tokens=MockTokens(self); self.reranking=MockReranking(self); self.tooling=MockTooling(self)
        logger.info("Strict offline mock-only discovery enabled")

    def is_configured(self):
        return bool(self.config.enabled)

    def request_key(self, payload): return hashlib.sha256(repr(payload).encode()).hexdigest()[:24]
    def _raise_fault(self, operation: str) -> None:
        scenario = self.scenario
        if not scenario.error_type:
            return
        
        if scenario.fail_operations and operation not in scenario.fail_operations:
            return

        if scenario.fail_next is not None:
            if scenario.fail_next <= 0:
                return
            scenario.fail_next -= 1

        raise build_mock_error(
            provider_name=self.name,
            error_type=scenario.error_type,
            message=scenario.error_message,
            status_code=scenario.error_status_code,
            error_code=scenario.error_code,
        )

    async def _before_async(self, operation: str) -> None:
        self.state.count(operation)
        self._raise_fault(operation)
        if self.scenario.latency_ms:
            await asyncio.sleep(self.scenario.latency_ms / 1000)

    def _before(self, operation: str) -> None:
        # Chỉ ném lỗi nếu operation nằm trong danh sách fail_operations bắt buộc
        if operation in self.scenario.fail_operations:
            self._raise_fault(operation)
            
        # Hoặc xử lý đếm số lần fail_next
        if self.scenario.fail_next is not None and self.scenario.fail_next > 0:
            self.scenario.fail_next -= 1
            self._raise_fault(operation)

    
    def _check_stream_fault(self, operation: str, phase: str, chunk_number: int = 0) -> None:
        """Kiểm tra và kích hoạt lỗi stream theo 3 giai đoạn."""
        scenario = self.scenario
        if not scenario.error_type:
            return

        # 1. PRE-STREAM: Lỗi xảy ra TRƯỚC KHI stream bắt đầu (auth failure, connection refused...)
        if phase == "pre_stream":
            self.state.count(operation)
            # Nếu cấu hình ngắt giữa chừng (fail_after_chunks > 0), bỏ qua phase pre_stream
            if scenario.fail_after_chunks is not None and scenario.fail_after_chunks > 0:
                return
            # Ngược lại (fail_after_chunks == 0 hoặc không set), ném lỗi ngay lập tức
            self._raise_fault(operation)

        # 2. MID-STREAM: Lỗi xảy ra TRONG LÚC đang stream (rớt mạng giữa chừng)
        elif phase == "mid_stream":
            if scenario.fail_after_chunks is not None and scenario.fail_after_chunks > 0:
                if chunk_number >= scenario.fail_after_chunks:
                    self._raise_fault(operation)

        # 3. POST-STREAM: Lỗi xảy ra SAU KHI stream kết thúc (lỗi cleanup, timeout closing...)
        elif phase == "post_stream":
            if getattr(scenario, "fail_post_stream", False):
                self._raise_fault(operation)
                
    async def has_capability(self, model_name, capability, http_client, timeout): return capability in MODEL_CAPABILITIES.get(model_name,set())
    async def send(self,*args,**kwargs): raise ProviderError("Mock network I/O is forbidden",provider_name=self.name,error_code="mock_network_forbidden",is_network_error=True)
    async def send_stream(self,*args,**kwargs): raise ProviderError("Mock network streaming I/O is forbidden",provider_name=self.name,error_code="mock_network_forbidden",is_network_error=True)
    async def moderation(self,**kwargs): self._before("moderation"); return {"flagged":False,"categories":{},"scores":{},"mock":True}
    async def computer_use(self,**kwargs): self._before("computer_use"); return {"actions":[],"status":"completed","mock":True}
    async def provider_info(self,**kwargs): self._before("provider_info"); return {"name":"mock","version":"1.0","mock":True,"network":False,"models":list(MODEL_CAPABILITIES)}
    async def health(self,**kwargs): self._before("health"); return {"status":"ok","provider":"mock","mock":True,"network":False}
    def snapshot(self): return self.state.snapshot()
    def reset(self): self.state.reset(); self.scenario=MockScenario()
