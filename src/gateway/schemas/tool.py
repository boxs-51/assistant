from typing import Any, Dict, Literal, Optional
from pydantic import Field
from .base import GatewayBaseModel

# =================================================================
# 4. TOOL DEFINITION, CALL & RESULT
# =================================================================

class GatewayToolParameter(GatewayBaseModel):
    """Schema cho một tham số của tool."""
    type: str = Field(..., description="Kiểu dữ liệu của tham số (e.g., 'string', 'number', 'boolean').")
    description: Optional[str] = None
    required: bool = False

class GatewayToolDefinition(GatewayBaseModel):
    """Định nghĩa một tool để gửi lên cho model."""
    name: str
    description: str
    parameters: Dict[str, GatewayToolParameter] = Field(default_factory=dict)

class FunctionCall(GatewayBaseModel): # Giữ lại để tương thích cấu trúc của OpenAI/Gemini
    name: str
    arguments: str  # JSON String từ model

class GatewayToolCall(GatewayBaseModel):
    """Yêu cầu gọi tool từ model trả về."""
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall

class ToolResult(GatewayBaseModel):
    """Kết quả trả về cho model sau khi thực thi tool."""
    tool_call_id: str
    content: Any
    is_error: bool = False
