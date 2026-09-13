from typing import Any, Dict, List, Optional
from pydantic import Field
from .base import GatewayBaseModel
from .attachment import GatewayAttachment
from .message import GatewayMessage
from .tool import GatewayToolDefinition

# =================================================================
# 7. GATEWAY REQUEST DTO
# =================================================================

class RequestConfig(GatewayBaseModel):
    """Cấu hình tham số sinh văn bản (Generation Parameters) cho LLM."""
    temperature: Optional[float] = Field(default=None, description="Điều khiển tính sáng tạo của câu trả lời")
    top_p: Optional[float] = Field(default=None, description="Nucleus sampling")
    max_tokens: Optional[int] = Field(default=None, description="Số lượng token tối đa sinh ra")
    stream: bool = Field(default=False, description="Bật/Tắt chế độ streaming")
    presence_penalty: Optional[float] = Field(default=None, description="Phạt dựa trên sự xuất hiện của từ")
    frequency_penalty: Optional[float] = Field(default=None, description="Phạt dựa trên tần suất của từ")
    response_format: Dict[str, Any] = Field(default=None, description="Chọn định dạng trả về của LLM")

    
class RequestMetadata(GatewayBaseModel):
    """Metadata được phân tầng rõ ràng trong request."""
    user: Dict[str, Any] = Field(default_factory=dict)
    trace: Dict[str, Any] = Field(default_factory=dict)
    routing: Dict[str, Any] = Field(default_factory=dict)
    cache: Dict[str, Any] = Field(default_factory=dict)

class GatewayChatRequest(GatewayBaseModel):
    """DTO chuẩn hóa cho mọi request chat, để Adapter chỉ nhận một object duy nhất."""

    model: str = Field(..., description="Định danh model sử dụng (e.g., gpt-4o, claude-3-5-sonnet)")
    messages: List[GatewayMessage] = Field(..., description="Danh sách lịch sử hội thoại")
    session_id: Optional[str] = Field(default=None, description="ID của phiên hội thoại để duy trì ngữ cảnh. Nếu bỏ trống, một session mới sẽ được tạo.")
    tools: Optional[List[GatewayToolDefinition]] = Field(default=None, description="Danh sách công cụ hỗ trợ (Function Calling)")
    
    # Gom cụm các cấu hình và metadata
    config: RequestConfig = Field(default_factory=RequestConfig, description="Cấu hình tham số của request")
    metadata: RequestMetadata = Field(default_factory=RequestMetadata, description="Thông tin tracking và định tuyến")