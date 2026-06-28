# e:\assistant\src\gateway\schemas.py (File mới)
import time
import uuid
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import httpx

# Models cho Non-Streaming Response
class GatewayMessage(BaseModel):
    role: str
    content: str

class GatewayChoice(BaseModel):
    index: int
    message: GatewayMessage
    finish_reason: Optional[str] = None

class GatewayUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class GatewayResponse(BaseModel):
    """
    Mô hình response chuẩn hóa mà Gateway sẽ làm việc.
    Tương thích hoàn toàn với OpenAI's ChatCompletion object.
    """
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4()}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[GatewayChoice]
    usage: GatewayUsage
    
    # Giữ lại response gốc để debug hoặc các mục đích khác
    raw_response: Optional[httpx.Response] = Field(default=None, exclude=True)

    class Config:
        arbitrary_types_allowed = True

# Models cho Streaming Response
class GatewayStreamDelta(BaseModel):
    """Tương đương 'delta' object trong OpenAI stream."""
    role: Optional[str] = None
    content: Optional[str] = None

class GatewayStreamChoice(BaseModel):
    """Tương đương 'choices' object trong OpenAI stream."""
    index: int
    delta: GatewayStreamDelta
    finish_reason: Optional[str] = None

class GatewayStreamChunk(BaseModel):
    """
    Mô hình chunk chuẩn hóa cho streaming.
    Tương thích hoàn toàn với OpenAI's ChatCompletionChunk object.
    """
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4()}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[GatewayStreamChoice]

    def to_sse(self) -> str:
        """Chuyển đổi chunk thành định dạng Server-Sent Event (SSE)."""
        return f"data: {self.model_dump_json()}\n\n"
