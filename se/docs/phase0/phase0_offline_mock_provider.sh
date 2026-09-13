#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-$(pwd)}"
cd "$ROOT"

fail(){ echo "ERROR: $*" >&2; exit 1; }
[ -d .git ] || fail "Not a git repository: $ROOT"

echo "== Phase 0 Offline Mock Provider =="

TARGETS=(
  src/provider/mock/provider.py
  src/provider/mock/__init__.py
  src/provider/mock/errors.py
  src/provider/mock/scenarios.py
  src/provider/mock/state.py
  src/provider/discovery.py
  src/provider/policies/routing_policy.py
  src/infrastructure/config/schemas.py
  tests/providers/test_mock_provider.py
  tests/providers/test_mock_capabilities.py
  tests/providers/test_mock_faults.py
  tests/providers/test_mock_runtime_flow.py
  tests/e2e/test_v1_offline.py
  docs/phase0/PHASE0_OFFLINE_MOCK_PROVIDER_STATUS.md
)
MODIFIED="$(git status --porcelain -- "${TARGETS[@]}" || true)"
[ -z "$MODIFIED" ] || { echo "$MODIFIED"; fail "Phase-owned files have local changes. Commit/stash them first."; }

mkdir -p src/provider/mock tests/providers tests/e2e docs/phase0

cat > src/provider/mock/errors.py <<'PY'
from __future__ import annotations
from typing import Any

from ..exceptions import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)


def build_mock_error(*, provider_name: str, error_type: str, message: str,
                     status_code: int | None = None, error_code: str | None = None,
                     raw_response: Any = None) -> ProviderError:
    kind = error_type.strip().lower()
    if kind in {"auth", "unauthorized", "forbidden"}:
        code = 403 if kind == "forbidden" else 401
        return ProviderAuthenticationError(
            message=message, provider_name=provider_name,
            status_code=status_code or code,
            error_code=error_code or "mock_auth_error", raw_response=raw_response,
        )
    if kind in {"rate_limit", "ratelimit", "429"}:
        return ProviderRateLimitError(
            message=message, provider_name=provider_name,
            status_code=status_code or 429,
            error_code=error_code or "mock_rate_limit", raw_response=raw_response,
        )
    if kind in {"timeout", "unavailable", "service_unavailable", "503"}:
        return ProviderUnavailableError(
            message=message, provider_name=provider_name,
            status_code=status_code or 503,
            error_code=error_code or "mock_unavailable", raw_response=raw_response,
            is_network_error=(kind == "timeout"),
        )
    return ProviderError(
        message=message, provider_name=provider_name,
        status_code=status_code, error_code=error_code or "mock_error",
        raw_response=raw_response,
    )
PY

cat > src/provider/mock/scenarios.py <<'PY'
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class MockScenario:
    name: str = "success"
    latency_ms: int = 0
    error_type: str | None = None
    error_message: str = "mock fault"
    error_status_code: int | None = None
    error_code: str | None = None
    fail_operations: set[str] = field(default_factory=set)
    fail_next: int = 0
    fail_after_chunks: int | None = None
    stream_chunk_size: int = 1
    fixed_chat_response: str | None = None

    def clone(self) -> "MockScenario":
        return MockScenario(
            name=self.name,
            latency_ms=self.latency_ms,
            error_type=self.error_type,
            error_message=self.error_message,
            error_status_code=self.error_status_code,
            error_code=self.error_code,
            fail_operations=set(self.fail_operations),
            fail_next=self.fail_next,
            fail_after_chunks=self.fail_after_chunks,
            stream_chunk_size=self.stream_chunk_size,
            fixed_chat_response=self.fixed_chat_response,
        )
PY

cat > src/provider/mock/state.py <<'PY'
from __future__ import annotations

import hashlib
import threading
from collections import defaultdict
from copy import deepcopy
from typing import Any


class MockState:
    """Resettable deterministic in-memory state for offline tests."""

    def __init__(self, seed: str = "assistant-offline-mock"):
        self.seed = seed
        self._lock = threading.RLock()
        self._calls = defaultdict(int)
        self._files: dict[str, dict[str, Any]] = {}
        self._batches: dict[str, dict[str, Any]] = {}
        self._jobs: dict[str, dict[str, Any]] = {}

    def count(self, operation: str) -> int:
        with self._lock:
            self._calls[operation] += 1
            return self._calls[operation]

    def stable_id(self, namespace: str, value: str) -> str:
        digest = hashlib.sha256(
            f"{self.seed}:{namespace}:{value}".encode("utf-8")
        ).hexdigest()[:20]
        return f"mock-{namespace}-{digest}"

    @property
    def files(self):
        return self._files

    @property
    def batches(self):
        return self._batches

    @property
    def jobs(self):
        return self._jobs

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "seed": self.seed,
                "calls": dict(self._calls),
                "files": deepcopy(self._files),
                "batches": deepcopy(self._batches),
                "jobs": deepcopy(self._jobs),
            }

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()
            self._files.clear()
            self._batches.clear()
            self._jobs.clear()
PY

cat > src/provider/mock/provider.py <<'PY'
"""Deterministic, zero-network provider for offline unit/runtime/API tests."""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import time
from typing import Any, AsyncGenerator, Dict
from io import BytesIO

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
from ...infrastructure.config import settings

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
    def __init__(self, provider): self.provider = provider

    @staticmethod
    def _text(body):
        for message in reversed(body.get("messages") or []):
            if message.get("role") == "user":
                value = message.get("content", "")
                return value if isinstance(value, str) else str(value)
        return ""

    async def chat(self, **kwargs):
        self.provider._before("chat")
        body = kwargs.get("body") or {}
        model = body.get("model") or MOCK_CHAT_MODEL
        text = self._text(body)
        answer = self.provider.scenario.fixed_chat_response or (f"mock:{text}" if text else "mock:ok")
        prompt_tokens = max(1, len(text.split()))
        completion_tokens = max(1, len(answer.split()))
        return GatewayResponse(
            id=self.provider.state.stable_id("chat", self.provider.request_key(body)),
            model=model, provider=self.provider.name,
            choices=[GatewayChoice(index=0, message=GatewayMessage(role="assistant", content=answer), finish_reason="stop")],
            usage=GatewayUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens),
            metadata={"mock": True, "scenario": self.provider.scenario.name},
        )

    async def chat_stream(self, **kwargs) -> AsyncGenerator[GatewayStreamChunk, None]:
        self.provider._before("chat_stream")
        body = kwargs.get("body") or {}
        model = body.get("model") or MOCK_CHAT_MODEL
        text = self._text(body)
        answer = self.provider.scenario.fixed_chat_response or (f"mock:{text}" if text else "mock:ok")
        words = answer.split()
        size = max(1, self.provider.scenario.stream_chunk_size)
        chunk_number = 0
        request_key = self.provider.request_key(body)
        for start in range(0, len(words), size):
            if self.provider.scenario.fail_after_chunks is not None and chunk_number >= self.provider.scenario.fail_after_chunks:
                self.provider._raise_fault("chat_stream")
            part = " ".join(words[start:start + size])
            if start + size < len(words): part += " "
            if self.provider.scenario.latency_ms:
                await asyncio.sleep(self.provider.scenario.latency_ms / 1000)
            yield GatewayStreamChunk(
                id=self.provider.state.stable_id("stream", request_key), model=model, provider=self.provider.name,
                choices=[GatewayStreamChoice(index=0, delta=GatewayStreamDelta(content=part, role="assistant" if start == 0 else None), finish_reason="stop" if start + size >= len(words) else None)],
                metadata={"mock": True, "scenario": self.provider.scenario.name},
            )
            chunk_number += 1


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
    def __init__(self, provider): self.provider = provider
    async def list_files(self, **kwargs):
        self.provider._before("files.list")
        return {"object":"list", "data":[{k:v for k,v in x.items() if k != "bytes"} for x in self.provider.state.files.values()], "next_page_token":None}
    async def upload_file(self, **kwargs):
        self.provider._before("files.upload")
        stream = kwargs["file_stream"]
        content = stream.read()
        display = kwargs.get("display_name") or "file"
        file_id = self.provider.state.stable_id("file", display + ":" + hashlib.sha1(content).hexdigest())
        self.provider.state.files[file_id] = {"name":file_id, "display_name":display, "mime_type":kwargs.get("mime_type") or "application/octet-stream", "size":len(content), "bytes":content, "created":0}
        return {k:v for k,v in self.provider.state.files[file_id].items() if k != "bytes"}
    async def get_file(self, **kwargs):
        self.provider._before("files.get")
        file_id = kwargs.get("file_id") or kwargs.get("file_name")
        return {k:v for k,v in self.provider.state.files[file_id].items() if k != "bytes"}
    async def download_file(self, **kwargs):
        self.provider._before("files.download")
        file_id = kwargs.get("file_id") or kwargs.get("file_name")
        return self.provider.state.files[file_id]["bytes"]
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
    def __init__(self, *, seed="assistant-offline-mock", scenario: MockScenario | None = None):
        super().__init__(provider_name="mock", auth_strategy=NoAuth(), endpoint_builder=EndpointBuilder(base_url="http://mock.invalid"), api_mapper=ApiTypeMapper(api_map={}), model_mapper=ModelMapper(model_map={m:m for m in MODEL_CAPABILITIES}), capability_manager=ModelCapabilityManager(provider_name="mock"))
        self.state=MockState(seed); self.scenario=(scenario or MockScenario()).clone()
        self.chat=MockChat(self); self.models=MockModels(self); self.embeddings=MockEmbeddings(self); self.files=MockFiles(self)
        self.audio=MockAudio(self); self.vision=MockVision(self); self.image=MockImage(self); self.video=MockVideo(self)
        self.batch=MockBatch(self); self.tokens=MockTokens(self); self.reranking=MockReranking(self); self.tooling=MockTooling(self)

    @classmethod
    def is_configured(cls):
        env=os.getenv("GATEWAY_PROVIDER__MOCK_ENABLED","").lower() in {"1","true","yes","on"}
        try: return env or bool(settings.provider.mock_enabled)
        except Exception: return env

    def request_key(self, payload): return hashlib.sha256(repr(payload).encode()).hexdigest()[:24]
    def _raise_fault(self, operation):
        s=self.scenario
        if not s.error_type or (s.fail_operations and operation not in s.fail_operations): return
        if s.fail_next > 0: s.fail_next -= 1; return
        raise build_mock_error(provider_name=self.name,error_type=s.error_type,message=s.error_message,status_code=s.error_status_code,error_code=s.error_code)
    def _before(self, operation):
        self.state.count(operation); self._raise_fault(operation)
        if self.scenario.latency_ms: time.sleep(self.scenario.latency_ms/1000)
    async def has_capability(self, model_name, capability, http_client, timeout): return capability in MODEL_CAPABILITIES.get(model_name,set())
    async def send(self,*args,**kwargs): raise ProviderError("Mock network I/O is forbidden",provider_name=self.name,error_code="mock_network_forbidden",is_network_error=True)
    async def send_stream(self,*args,**kwargs): raise ProviderError("Mock network streaming I/O is forbidden",provider_name=self.name,error_code="mock_network_forbidden",is_network_error=True)
    async def moderation(self,**kwargs): self._before("moderation"); return {"flagged":False,"categories":{},"scores":{},"mock":True}
    async def computer_use(self,**kwargs): self._before("computer_use"); return {"actions":[],"status":"completed","mock":True}
    async def provider_info(self,**kwargs): self._before("provider_info"); return {"name":"mock","version":"1.0","mock":True,"network":False,"models":list(MODEL_CAPABILITIES)}
    async def health(self,**kwargs): self._before("health"); return {"status":"ok","provider":"mock","mock":True,"network":False}
    def snapshot(self): return self.state.snapshot()
    def reset(self): self.state.reset(); self.scenario=MockScenario()
PY

cat > src/provider/mock/__init__.py <<'PY'
from .errors import build_mock_error
from .provider import (
    MODEL_CAPABILITIES, MOCK_MODEL, MOCK_CHAT_MODEL, MOCK_EMBEDDING_MODEL,
    MOCK_VISION_MODEL, MOCK_AUDIO_MODEL, MOCK_IMAGE_MODEL, MOCK_VIDEO_MODEL,
    MOCK_TOOL_MODEL, MOCK_BATCH_MODEL, MOCK_RERANK_MODEL, MOCK_REASONING_MODEL,
    MockProvider,
)
from .scenarios import MockScenario
from .state import MockState

__all__ = [
    "MockProvider", "MockScenario", "MockState", "build_mock_error",
    "MODEL_CAPABILITIES", "MOCK_MODEL", "MOCK_CHAT_MODEL", "MOCK_EMBEDDING_MODEL",
    "MOCK_VISION_MODEL", "MOCK_AUDIO_MODEL", "MOCK_IMAGE_MODEL", "MOCK_VIDEO_MODEL",
    "MOCK_TOOL_MODEL", "MOCK_BATCH_MODEL", "MOCK_RERANK_MODEL", "MOCK_REASONING_MODEL",
]
PY

./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p=Path('src/provider/discovery.py')
s=p.read_text(encoding='utf-8')
start=s.index('        for name, provider_class in ProviderFactory._provider_classes.items():')
end=len(s)
prefix=s[:start]
new='''        for name, provider_class in ProviderFactory._provider_classes.items():\n            # Mock is intentionally opt-in. Real providers use the same rule:\n            # an unconfigured provider is never registered.\n            if not provider_class.is_configured():\n                logger.info("Provider skipped because it is not configured", provider=name)\n                continue\n            provider_instance = ProviderFactory.create_provider(name)\n            if provider_instance:\n                self.registry.register(provider_instance)\n'''
p.write_text(prefix+new,encoding='utf-8')
PY

./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p=Path('src/provider/policies/routing_policy.py')
s=p.read_text(encoding='utf-8')
old='''        self._default_chain = [self.providers[name] for name in settings.provider.priority if name in self.providers]\n        #if "mock" in self.providers and "mock" not in [p.name for p in self._default_chain]:\n            # Offline Phase 0 mode: mock is appended only when explicitly enabled.\n        #    self._default_chain.append(self.providers["mock"])\n'''
new='''        self._default_chain = [\n            self.providers[name]\n            for name in settings.provider.priority\n            if name in self.providers\n        ]\n        if settings.provider.mock_enabled and settings.provider.priority == ["mock"] and "mock" in self.providers:\n            self._default_chain = [self.providers["mock"]]\n'''
if old not in s: raise SystemExit('routing policy block not found')
p.write_text(s.replace(old,new),encoding='utf-8')
PY

./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p=Path('src/infrastructure/config/schemas.py')
s=p.read_text(encoding='utf-8')
old='''class ProviderSettings(BaseModel):\n    timeout: int = 60\n    mock_enabled: bool = False\n    retry: int = 2\n    enable_fallback: bool = True\n    priority: list[str] = Field(default=["openai", "anthropic", "gemini", "ollama"])\n    routing_rules_path: str = "config/routing/routing_rules.yaml"\n'''
new='''class ProviderSettings(BaseModel):\n    timeout: int = 60\n    mock_enabled: bool = False\n    mock_seed: str = "assistant-offline-mock"\n    mock_scenario: str = "success"\n    retry: int = 2\n    enable_fallback: bool = True\n    priority: list[str] = Field(default=["openai", "anthropic", "gemini", "ollama"])\n    routing_rules_path: str = "config/routing/routing_rules.yaml"\n'''
if old not in s: raise SystemExit('ProviderSettings block not found')
p.write_text(s.replace(old,new),encoding='utf-8')
PY

cat > tests/providers/test_mock_capabilities.py <<'PY'
import pytest
from src.domain.schemas import ModelCapability
from src.provider.mock import MODEL_CAPABILITIES, MockProvider

@pytest.mark.asyncio
@pytest.mark.parametrize("model,capability", [
    ("mock-chat", ModelCapability.CHAT),
    ("mock-embedding", ModelCapability.EMBEDDINGS),
    ("mock-vision", ModelCapability.VISION),
    ("mock-audio", ModelCapability.SPEECH_TO_TEXT),
    ("mock-image", ModelCapability.IMAGE_GENERATION),
    ("mock-video", ModelCapability.VIDEO_GENERATION),
    ("mock-batch", ModelCapability.CHAT_BATCH),
    ("mock-rerank", ModelCapability.RERANK),
])
async def test_capability_matrix(model, capability):
    provider=MockProvider()
    assert capability in MODEL_CAPABILITIES[model]
    assert await provider.has_capability(model, capability, None, 1) is True

@pytest.mark.asyncio
async def test_all_mock_capabilities_are_offline():
    p=MockProvider()
    assert (await p.audio.speech_to_text())["mock"]
    assert (await p.audio.text_to_speech(text="hello"))["mock"]
    assert (await p.audio.audio_translation())["mock"]
    assert (await p.vision.vision())["mock"]
    assert (await p.image.image_generation())["mock"]
    assert (await p.image.image_edit())["mock"]
    assert (await p.image.image_variation())["mock"]
    assert (await p.video.video_generation())["mock"]
    assert (await p.video.video_understanding())["mock"]
    bid=(await p.batch.create_batch(input=[]))["id"]
    assert (await p.batch.batch_status(batch_id=bid))["status"] == "completed"
    assert (await p.tokens.count_tokens(text="one two"))["total_tokens"] == 2
    assert (await p.reranking.rerank(documents=["a","b"]))["mock"]
    assert (await p.tooling.web_search(query="x"))["mock"]
    assert (await p.tooling.code_execution(code="x"))["mock"]
    assert (await p.moderation(input="x"))["mock"]
    assert (await p.computer_use(action="click"))["mock"]
PY

cat > tests/providers/test_mock_faults.py <<'PY'
import pytest
from src.provider.mock import MockProvider, MockScenario
from src.provider.exceptions import ProviderRateLimitError, ProviderUnavailableError

@pytest.mark.asyncio
async def test_rate_limit_fault():
    p=MockProvider(scenario=MockScenario(name="rate",error_type="rate_limit",fail_operations={"chat"}))
    with pytest.raises(ProviderRateLimitError):
        await p.chat.chat(body={"model":"mock-chat","messages":[{"role":"user","content":"x"}]})

@pytest.mark.asyncio
async def test_fail_next_is_consumed():
    p=MockProvider(scenario=MockScenario(error_type="rate_limit",fail_operations={"chat"},fail_next=1))
    ok=await p.chat.chat(body={"model":"mock-chat","messages":[{"role":"user","content":"x"}]})
    assert ok.provider == "mock"
    assert p.snapshot()["calls"]["chat"] == 1

@pytest.mark.asyncio
async def test_stream_fault_after_first_chunk():
    p=MockProvider(scenario=MockScenario(name="stream-fail",error_type="service_unavailable",fail_after_chunks=1))
    chunks=[]
    with pytest.raises(ProviderUnavailableError):
        async for c in p.chat.chat_stream(body={"model":"mock-chat","messages":[{"role":"user","content":"one two three"}]}):
            chunks.append(c)
    assert chunks
PY

cat > tests/providers/test_mock_provider.py <<'PY'
import pytest
from io import BytesIO
from src.domain.schemas import ModelCapability
from src.provider.mock import MockProvider

@pytest.mark.asyncio
async def test_chat_is_deterministic():
    p=MockProvider()
    body={"model":"mock-chat","messages":[{"role":"user","content":"hello world"}]}
    a=await p.chat.chat(body=body); b=await p.chat.chat(body=body)
    assert a.id == b.id
    assert a.choices[0].message.content == "mock:hello world"
    assert await p.has_capability("mock-chat", ModelCapability.CHAT, None, 1)

@pytest.mark.asyncio
async def test_embeddings_are_deterministic():
    p=MockProvider()
    body={"model":"mock-embedding","input":["hello"]}
    assert await p.embeddings.embeddings(body=body) == await p.embeddings.embeddings(body=body)

@pytest.mark.asyncio
async def test_files_round_trip_and_reset():
    p=MockProvider(seed="test")
    f=await p.files.upload_file(file_stream=BytesIO(b"hello"),file_size=5,mime_type="text/plain",display_name="a.txt")
    fid=f["name"]
    assert await p.files.download_file(file_id=fid) == b"hello"
    assert await p.files.delete_file(file_id=fid)
    p.reset()
    assert p.snapshot()["files"] == {}

@pytest.mark.asyncio
async def test_network_guard():
    p=MockProvider()
    with pytest.raises(Exception) as exc:
        await p.send(None,"whatever",timeout=1)
    assert "mock_network_forbidden" in str(exc.value.error_code)
PY

cat > tests/providers/test_mock_runtime_flow.py <<'PY'
import httpx
import pytest
from src.circuit_breaker import CircuitBreakerManager
from src.infrastructure.config import ConfigurationRegistry
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.provider.discovery import ProviderDiscovery
from src.provider.policies.routing_policy import RoutingPolicy
from src.provider.registry import ProviderRegistry
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler

@pytest.mark.asyncio
async def test_full_provider_handler_path():
    ConfigurationRegistry.set_config(ConfigSchema(provider=ProviderSettings(priority=["mock"],mock_enabled=True,timeout=5)))
    registry=ProviderRegistry(); ProviderDiscovery(registry).run(); providers=registry.list_all_providers()
    assert list(providers) == ["mock"]
    breaker=CircuitBreakerManager(); executor=ProviderExecutor(breaker); routing=RoutingPolicy(providers)
    client=httpx.AsyncClient()
    kwargs=dict(providers=providers,routing_policy=routing,executor=executor,circuit_breaker_manager=breaker)
    try:
        chat=await ChatExecutionHandler(**kwargs).execute_with_fallback(client,{"model":"mock-chat","messages":[{"role":"user","content":"phase0"}]})
        emb=await EmbeddingExecutionHandler(**kwargs).execute(client,{"model":"mock-embedding","input":["phase0"]})
        models=await ModelOperationHandler(**kwargs).execute("mock",None,client)
        f=await FileOperationHandler(**kwargs).execute({"action":"upload","provider_name":"mock","file_bytes":b"x","file_size":1,"display_name":"x"},client)
        assert chat.provider=="mock" and emb["data"] and models.data and f["name"]
    finally:
        await client.aclose()
PY

cat > tests/e2e/test_v1_offline.py <<'PY'
"""Canonical API v1 E2E with zero external AI/network provider traffic."""
from __future__ import annotations
from types import SimpleNamespace
from io import BytesIO

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.schemas.identity import Identity
from src.infrastructure.config.schemas import ConfigSchema, ProviderSettings
from src.provider.mock import MockProvider
from src.provider.executor import ProviderExecutor
from src.provider.handlers.chat_handler import ChatExecutionHandler
from src.provider.handlers.embedding_handler import EmbeddingExecutionHandler
from src.provider.handlers.model_handler import ModelOperationHandler
from src.provider.handlers.file_handler import FileOperationHandler
from src.provider.policies.routing_policy import RoutingPolicy
from src.circuit_breaker import CircuitBreakerManager
from src.transport.gateway.api.v1 import admin, agent_router, auth_router, chat_router, embeddings_router, events_router, files_router, health_router, models_router, multi_agent_router, tool_router
from src.transport.gateway.api.v1.auth_router import get_auth_facade, get_api_key_service
from src.transport.gateway.authentication.dependency import get_current_identity, verify_admin_ip
from src.transport.gateway.dependencies import get_container

class InlineEventBus:
    def __init__(self): self.handlers={}
    def subscribe(self,n,h): self.handlers.setdefault(n,[]).append(h)
    def unsubscribe(self,n,h):
        if h in self.handlers.get(n,[]): self.handlers[n].remove(h)
    async def publish(self,event):
        for h in list(self.handlers.get(event.event_name,[])): await h(event)

class FakeWS:
    def __init__(self): self.connected=[]; self.subscriptions={}
    async def connect(self,ws): self.connected.append(ws)
    def disconnect(self,ws):
        if ws in self.connected: self.connected.remove(ws)
    async def subscribe(self,ws,event): self.subscriptions.setdefault(ws,set()).add(event)
    async def unsubscribe(self,ws,event): self.subscriptions.get(ws,set()).discard(event)
    async def shutdown(self): pass

class FakeAuthFacade:
    async def initiate_registration(self,data): return {"status":"pending","email":str(data.email)}
    async def confirm_registration(self,email,otp):
        from src.domain.schemas.auth import TokenSchema; return TokenSchema(access_token="offline",refresh_token="offline-refresh")
    async def login(self,data):
        from src.domain.schemas.auth import TokenSchema; return TokenSchema(access_token="offline",refresh_token="offline-refresh")
    async def refresh_access_token(self,token):
        from src.domain.schemas.auth import AccessTokenSchema; return AccessTokenSchema(access_token="offline")
    async def logout(self,token): return None
    async def handle_oauth_callback(self,provider,user):
        from src.domain.schemas.auth import TokenSchema; return TokenSchema(access_token="offline-oauth",refresh_token="offline-refresh")
    async def get_current_user_info(self,identity):
        from src.domain.schemas.auth import UserMeSchema; return UserMeSchema(id=identity.user_id or "offline-user",email="offline@example.com",roles=["member"])

class FakeAPIKeys:
    async def create_api_key(self,data,identity): return {"id":"mock-key","full_key":"mock-api-key","prefix":"mock","created_at":"1970-01-01T00:00:00Z"}
    async def list_api_keys(self,identity): return []
    async def revoke_api_key(self,key_id,identity): return True

class FakeOAuthClient:
    async def authorize_redirect(self,request,redirect_uri):
        from fastapi.responses import RedirectResponse; return RedirectResponse(str(redirect_uri))
    async def authorize_access_token(self,request): return {"access_token":"offline"}
    async def userinfo(self,token): return {"email":"offline@example.com","sub":"offline-user","name":"Offline"}
    async def get(self,*args,**kwargs): return httpx.Response(200,json=[])
class FakeOAuth:
    _clients={"mock":object()}
    def create_client(self,provider): return FakeOAuthClient()

class FakeCoordinator:
    def __init__(self): self.sessions={}; self.messages={}; self.tasks={}; self.executor=None
    async def create_session_async(self,identity,agent_ids):
        sid=f"mock-session-{len(self.sessions)+1}"; self.sessions[sid]={"session_id":sid,"owner_user_id":identity.user_id,"agent_ids":list(agent_ids),"status":"ACTIVE"}; return self.sessions[sid]
    def add_agent(self,sid,aid,identity): self.sessions[sid]["agent_ids"].append(aid); return self.sessions[sid]
    def list_messages(self,sid,identity): return self.messages.get(sid,[])
    async def send_message_async(self,**kw):
        item={"message_id":f"mock-message-{len(self.messages.get(kw['session_id'],[]))+1}",**kw}; self.messages.setdefault(kw['session_id'],[]).append(item); return item
    async def create_task_async(self,**kw):
        tid=f"mock-task-{len(self.tasks)+1}"; item={"task_id":tid,"status":"CREATED",**kw}; self.tasks[tid]=item; return item
    def get_task(self,tid,identity): return self.tasks[tid]
    def cancel_task(self,tid,identity): self.tasks[tid]["status"]="CANCELLED"; return self.tasks[tid]
    def close_session(self,sid,identity): self.sessions[sid]["status"]="CLOSED"; return self.sessions[sid]
    async def execute_task(self,tid,identity,executor): return await executor(self.tasks[tid])
    def get_execution(self,eid,identity): return {"execution_id":eid,"status":"completed"}

@pytest.fixture
def offline_app():
    app=FastAPI()
    identity=Identity(auth_type="jwt",user_id="offline-user",permissions=["admin:read","admin:write"],scopes={"profile","email"})
    bus=InlineEventBus(); ws=FakeWS(); provider=MockProvider(); breakers=CircuitBreakerManager(); executor=ProviderExecutor(breakers); providers={"mock":provider}; routing=RoutingPolicy(providers)
    h=dict(providers=providers,routing_policy=routing,executor=executor,circuit_breaker_manager=breakers)
    runtime=SimpleNamespace(providers=providers,routing_policy=routing,circuit_breaker_manager=breakers,chat_handler=ChatExecutionHandler(**h),embedding_handler=EmbeddingExecutionHandler(**h),model_handler=ModelOperationHandler(**h),file_handler=FileOperationHandler(**h))
    agent_registry=SimpleNamespace(_items={},register=lambda x: agent_registry._items.__setitem__(x.name,x),get=lambda x: agent_registry._items.get(x))
    tool_registry=SimpleNamespace(_items={},register=lambda x: tool_registry._items.__setitem__(x.name,x),get=lambda x: tool_registry._items.get(x))
    container=SimpleNamespace(config=ConfigSchema(provider=ProviderSettings(priority=["mock"],mock_enabled=True)),storage=SimpleNamespace(drivers={},repositories={}),http_client=httpx.AsyncClient(),eventing_manager=SimpleNamespace(bus=bus,ws_manager=ws),provider_runtime=runtime,circuit_breaker_manager=breakers,agent_registry=agent_registry,tool_registry=tool_registry,multi_agent_coordinator=FakeCoordinator(),oauth=FakeOAuth())
    for r in [auth_router.router,files_router.router,models_router.router,chat_router.router,embeddings_router.router,admin.router,agent_router.router,tool_router.router,events_router.router,multi_agent_router.router,health_router.router]: app.include_router(r)
    app.dependency_overrides[get_container]=lambda:container
    app.dependency_overrides[get_current_identity]=lambda:identity
    app.dependency_overrides[verify_admin_ip]=lambda:None
    app.dependency_overrides[get_auth_facade]=lambda:FakeAuthFacade()
    app.dependency_overrides[get_api_key_service]=lambda:FakeAPIKeys()
    yield app,container
    import asyncio; asyncio.get_event_loop().run_until_complete(container.http_client.aclose())

@pytest.mark.asyncio
async def test_v1_provider_apis(offline_app):
    app,_=offline_app
    transport=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,base_url="http://testserver") as c:
        assert (await c.post("/v1/chat/completions",json={"model":"mock-chat","provider":"mock","messages":[{"role":"user","content":"hello"}],"config":{"stream":False}})).status_code==200
        assert (await c.post("/v1/embeddings",json={"model":"mock-embedding","provider":"mock","input":["hello"]})).status_code==200
        models=await c.get("/v1/models/",params={"provider_name":"mock"}); assert models.status_code==200
        detail=await c.get("/v1/models/mock-chat",params={"provider_name":"mock"}); assert detail.status_code==200
        up=await c.post("/v1/files/",params={"provider_name":"mock","display_name":"x.txt"},files={"file":("x.txt",b"hello","text/plain")}); assert up.status_code in (200,201)
        fid=up.json()["name"]
        assert (await c.get(f"/v1/files/{fid}",params={"provider_name":"mock","action":"metadata"})).status_code==200
        d=await c.get(f"/v1/files/{fid}",params={"provider_name":"mock","action":"download"}); assert d.content==b"hello"
        assert (await c.delete(f"/v1/files/{fid}",params={"provider_name":"mock"})).status_code==204

@pytest.mark.asyncio
async def test_v1_auth_api(offline_app):
    app,_=offline_app; t=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t,base_url="http://testserver") as c:
        assert (await c.post("/v1/auth/register/initiate",json={"email":"offline@example.com","password":"secret123","name":"Offline"})).status_code==200
        assert (await c.post("/v1/auth/register/verify",json={"email":"offline@example.com","otp":"123456"})).status_code==200
        assert (await c.post("/v1/auth/login",json={"email":"offline@example.com","password":"secret123"})).status_code==200
        assert (await c.post("/v1/auth/refresh",json={"refresh_token":"offline-refresh"})).status_code==200
        assert (await c.post("/v1/auth/logout",json={"refresh_token":"offline-refresh"})).status_code==204
        assert (await c.get("/v1/auth/me")).status_code==200
        assert (await c.post("/v1/auth/api-keys",json={"name":"x"})).status_code==201
        assert (await c.get("/v1/auth/api-keys")).status_code==200
        assert (await c.delete("/v1/auth/api-keys/mock-key")).status_code==204
        oauth=await c.post("/v1/auth/oauth/mock",json={"provider":"mock","provider_user_id":"offline-user","email":"offline@example.com"}); assert oauth.status_code==200
        redirect=await c.get("/v1/auth/oauth/login/mock",follow_redirects=False); assert redirect.status_code in (302,307)

@pytest.mark.asyncio
async def test_v1_agent_tool_admin_health_multi_agent(offline_app):
    app,_=offline_app; t=httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t,base_url="http://testserver") as c:
        tool={"name":"offline.tool","description":"offline","parameters":{"type":"object","properties":{}}}
        assert (await c.post("/v1/tools/",json=tool)).status_code==201
        agent={"name":"offline-agent","goal":"test","instruction":"test","tools":["offline.tool"]}
        assert (await c.post("/v1/agents/",json=agent)).status_code==201
        s=await c.post("/v1/multi-agent/sessions",json={"agent_ids":[]}); assert s.status_code==201; sid=s.json()["session_id"]
        assert (await c.post(f"/v1/multi-agent/sessions/{sid}/agents",json={"agent_id":"offline-agent"})).status_code==200
        assert (await c.get(f"/v1/multi-agent/sessions/{sid}/messages")).status_code==200
        m=await c.post("/v1/multi-agent/messages",json={"session_id":sid,"sender_id":"offline-user","payload":{"x":1}}); assert m.status_code==201
        task=await c.post("/v1/multi-agent/tasks",json={"session_id":sid,"assigned_agent_id":"offline-agent","input":{"prompt":"hi"}}); assert task.status_code==201; tid=task.json()["task_id"]
        assert (await c.get(f"/v1/multi-agent/tasks/{tid}")).status_code==200
        assert (await c.post(f"/v1/multi-agent/tasks/{tid}/cancel")).status_code==200
        assert (await c.post(f"/v1/multi-agent/sessions/{sid}/close")).status_code==200
        assert (await c.get("/v1/multi-agent/executions/ex1")).status_code==200
        assert (await c.post("/v1/admin/reload/routing")).status_code==200
        assert (await c.get("/v1/admin/circuit-breakers/status")).status_code==200
        assert (await c.get("/health")).status_code==200
        assert (await c.get("/ready")).status_code==200
        assert (await c.get("/metrics")).status_code==200
        assert (await c.get("/stats")).status_code==200

def test_v1_events_websocket(offline_app):
    app,_=offline_app
    with TestClient(app) as client:
        with client.websocket_connect("/v1/events/ws") as ws:
            ws.send_json({"action":"subscribe","event_name":"mock.event"})
            msg=ws.receive_json(); assert msg["status"]=="success"
            ws.send_json({"action":"unsubscribe","event_name":"mock.event"})
            msg=ws.receive_json(); assert msg["status"]=="success"
PY

cat > docs/phase0/PHASE0_OFFLINE_MOCK_PROVIDER_STATUS.md <<'MD'
# Phase 0 — Offline Mock Provider

## Goal

A deterministic, configurable, zero-network provider for tests and local E2E.
No OpenAI/Gemini/Anthropic API key, Ollama server, or outbound provider HTTP is
required for the mocked execution path.

## Scope

- Chat + streaming chat.
- Embeddings.
- Model catalog.
- File lifecycle.
- Audio STT/TTS/translation.
- Vision/OCR.
- Image generation/edit/variation.
- Video generation/understanding.
- Batch operations.
- Token counting.
- Reranking.
- Tool/web-search/code-execution stubs.
- Moderation and computer-use deterministic stubs.
- Provider metadata and health.
- Capability matrix.
- Deterministic IDs/state with reset/snapshot.
- Fault injection and stream-failure injection.
- Latency injection.
- Explicit network-I/O guard.
- Opt-in discovery.
- ProviderRuntime handler integration tests.
- Canonical `/v1` HTTP/WebSocket E2E tests.

## Invariants

1. `mock_enabled=false` does not register the mock provider.
2. `MockProvider.send()` and `send_stream()` always fail with
   `mock_network_forbidden`; accidental provider HTTP I/O therefore cannot be
   silently introduced into an offline test.
3. Identical request + seed produces identical response identifiers/data.
4. `reset()` clears mutable provider state.

## Verification

```bash
pytest -q \
  tests/providers/test_mock_provider.py \
  tests/providers/test_mock_capabilities.py \
  tests/providers/test_mock_faults.py \
  tests/providers/test_mock_runtime_flow.py \
  tests/e2e/test_v1_offline.py
```
MD

./.venv/Scripts/python.exe -m compileall -q src/provider/mock src/provider/discovery.py src/provider/policies/routing_policy.py src/infrastructure/config/schemas.py tests/providers tests/e2e/test_v1_offline.py
git diff --check

echo "Phase Offline Mock Provider applied."
