from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from .base import GatewayBaseModel


class AgentMemoryConfig(GatewayBaseModel):
    """
    Cấu hình bộ nhớ cho Agent.
    """
    type: str = Field("conversation_window", description="Loại bộ nhớ, ví dụ: 'conversation_window', 'summary'.")
    window_size: int = Field(10, description="Kích thước cửa sổ cho conversation_window memory.")


class AgentDefinition(GatewayBaseModel):
    """
    Schema định nghĩa một Agent mà Client đăng ký với Gateway.
    Đây là "bản thiết kế" mà Gateway sẽ sử dụng để thực thi Agent.
    """
    name: str = Field(..., description="Tên định danh duy nhất của Agent, ví dụ: 'GithubAgent', 'SalesAgent'.")
    goal: str = Field(..., description="Mục tiêu chính, mô tả cấp cao về nhiệm vụ của Agent.")
    instruction: str = Field(..., description="System prompt hoặc chỉ dẫn chi tiết cho Agent thực thi.")
    tools: List[str] = Field(default_factory=list, description="Danh sách tên các Tool đã đăng ký mà Agent này được phép sử dụng.")
    workflow_definition: Optional[Dict[str, Any]] = Field(None, description="Cấu trúc workflow (dành cho tương lai, ví dụ: định nghĩa các bước theo YAML/JSON).")
    memory_config: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig, description="Cấu hình bộ nhớ cho Agent.")


class AgentRegistrationResponse(GatewayBaseModel):
    status: str = "success"
    name: str
    message: str