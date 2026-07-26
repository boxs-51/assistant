from typing import Any, Dict, List, Optional
from pydantic import Field
from .base import GatewayBaseModel
from .message import GatewayMessage
from .tool import GatewayToolCall
from .enums import FinishReason
from .usage import GatewayUsage
import time
# =================================================================
# 8. MỞ RỘNG GATEWAY RESPONSE & STREAM CHUNK
# =================================================================

class GatewayChoice(GatewayBaseModel):
    index: int
    message: GatewayMessage
    finish_reason: Optional[FinishReason] = None

class GatewayResponse(GatewayBaseModel):
    """Phản hồi Non-Streaming hoàn chỉnh."""
    id: str = Field(default_factory=str)
    model: str
    choices: List[GatewayChoice] = Field(default_factory=list)
    usage: GatewayUsage = Field(default_factory=GatewayUsage)
    provider: str = Field(..., description="Provider thực tế đã xử lý request")
    created: int = Field(default_factory=lambda: int(time.time()))
    object: str = "gateway_response"
    system_fingerprint: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    raw_response: Optional[Any] = Field(default=None, exclude=True)

class GatewayStreamDelta(GatewayBaseModel):
    """Nội dung thay đổi trong một chunk stream."""
    content: Optional[str] = None
    role: Optional[str] = None
    tool_calls: Optional[List[GatewayToolCall]] = None # Hỗ trợ streaming tool call
    
class GatewayStreamChoice(GatewayBaseModel):
    index: int
    delta: GatewayStreamDelta
    finish_reason: Optional[str] = None

class GatewayStreamChunk(GatewayBaseModel):
    """Phản hồi Streaming hoàn chỉnh theo chuẩn SSE."""
    id: str = Field(default_factory=str)
    model: str
    choices: List[GatewayStreamChoice]
    object: str = "gateway_stream_chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    provider: str
    usage: Optional[GatewayUsage] = None  # Thường trả về ở chunk cuối cùng
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    def to_sse(self) -> str:
        """Chuyển đổi sang chuẩn Server-Sent Event (SSE)."""
        return f"data: {self.model_dump_json(exclude_none=True)}\n\n"
