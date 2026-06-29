from pydantic import BaseModel, Field
from typing import List, Optional, Any

# =================================================================
# API DATA TRANSFER OBJECTS (DTOs)
# Các model này định nghĩa cấu trúc dữ liệu cho các yêu cầu và phản hồi API.
# =================================================================

# --- Cấu trúc cho Non-Streaming Response ---

class GatewayMessage(BaseModel):
    """Cấu trúc cho một tin nhắn hoàn chỉnh trong một cuộc hội thoại."""
    role: str
    content: Optional[str] = None

class GatewayChoice(BaseModel):
    """Cấu trúc cho một lựa chọn phản hồi hoàn chỉnh."""
    index: int
    message: GatewayMessage
    finish_reason: Optional[str] = None

class GatewayUsage(BaseModel):
    """Cấu trúc cho thông tin sử dụng token."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class GatewayResponse(BaseModel):
    """Định nghĩa cấu trúc cho một phản hồi hoàn chỉnh từ gateway."""
    id: str = Field(default_factory=str)
    model: str
    choices: List[GatewayChoice] = Field(default_factory=list)
    usage: GatewayUsage = Field(default_factory=GatewayUsage)
    # Trường này không trả về cho client, chỉ dùng nội bộ
    raw_response: Optional[Any] = Field(default=None, exclude=True)

# --- Cấu trúc cho Streaming Response ---

class GatewayStreamDelta(BaseModel):
    """Nội dung thay đổi trong một stream chunk."""
    content: Optional[str] = None
    role: Optional[str] = None

class GatewayStreamChoice(BaseModel):
    """Một lựa chọn trong một stream chunk."""
    index: int
    delta: GatewayStreamDelta
    finish_reason: Optional[str] = None

class GatewayStreamChunk(BaseModel):
    """Định nghĩa cấu trúc cho một chunk dữ liệu trong một phản hồi streaming."""
    id: str = Field(default_factory=str)
    model: str
    choices: List[GatewayStreamChoice]

    def to_sse(self) -> str:
        """Chuyển đổi chunk thành định dạng Server-Sent Event (SSE)."""
        return f"data: {self.model_dump_json()}\n\n"