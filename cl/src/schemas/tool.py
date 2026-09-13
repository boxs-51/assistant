from typing import Any, Dict, Literal, Optional
from .enums import ToolType
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
    """
    Định nghĩa một Tool chuẩn hóa trong hệ thống Gateway.
    Client không được thấy, chỉ dùng ngầm tại Server-Side Tool Injection.
    """
    name: str = Field(..., description="Tên định danh duy nhất của tool (Không chứa prefix).")
    description: str = Field(..., description="Mô tả chi tiết chức năng để LLM hiểu khi nào cần gọi.")
    
    # Sử dụng cấu trúc OpenAPI/JSON Schema chuẩn (Dict[str, Any]) thay vì bọc cứng qua lớp Parameter con
    # Điều này giúp tương thích 100% với inputSchema phức tạp (nested objects/arrays) từ MCP Server trả về.
    parameters: Optional[Dict[str, Any]] = Field(
        default=None, 
        description="JSON Schema định nghĩa các tham số đầu vào (type, properties, required, v.v.)."
    )
    
    tool_type: Optional[ToolType] = Field(default=None, description="Loại tool để Gateway biết cách điều phối thực thi.")
    source_server: Optional[str] = Field(
        default=None, 
        description="Tên của MCP Server phụ trách nếu tool_type là MCP (e.g., 'gdrive', 'github')."
    )
    
class FunctionCall(GatewayBaseModel):
    """Chi tiết hàm được gọi từ Model (Tương thích cấu trúc chuẩn OpenAI/Gemini)."""
    name: str = Field(..., description="Tên của tool được model chỉ định gọi.")
    arguments: str = Field(..., description="Chuỗi JSON String chứa các tham số truyền vào từ model.")

class GatewayToolCall(GatewayBaseModel):
    """Yêu cầu gọi tool từ Model trả về được Gateway Response Parser trích xuất ra."""
    id: str = Field(..., description="ID duy nhất của lượt gọi tool (Dùng để map kết quả trả về).")
    type: Literal["function"] = "function"
    function: FunctionCall

class GatewayToolResult(GatewayBaseModel):
    """Kết quả trả về sau khi Gateway ExecutorRegistry thực thi xong Tool vật lý."""
    tool_call_id: str = Field(..., description="Khớp với ID của GatewayToolCall tương ứng.")
    name: str = Field(..., description="Tên của tool vừa được thực thi.")
    content: str = Field(..., description="Nội dung kết quả thực thi (Thường là chuỗi Text/JSON String cho LLM đọc).")
    is_error: bool = Field(default=False, description="Đánh dấu nếu quá trình thực thi tool bị lỗi hệ thống.")
