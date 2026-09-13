from pydantic import Field
from typing import List, Optional, Dict, Any
from enum import Enum

from .base import GatewayBaseModel
from .message import GatewayMessage

class SessionStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"
    EXPIRED = "expired"
    ERROR = "error"

class Session(GatewayBaseModel):
    """
    Đại diện cho một phiên hội thoại (conversation session).
    Đối tượng này sẽ được lưu trữ (ví dụ: trong Redis) để duy trì trạng thái.
    """
    session_id: str = Field(..., description="ID duy nhất của phiên làm việc.")
    user_id: Optional[str] = Field(None, description="ID của người dùng sở hữu phiên này.")
    organization_id: Optional[str] = Field(None, description="ID của tổ chức.")
    messages: List[GatewayMessage] = Field(default_factory=list, description="Lịch sử các tin nhắn trong phiên.")
    status: SessionStatus = Field(default=SessionStatus.ACTIVE)
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Các thông tin ngữ cảnh khác.")
    created_at: float = Field(..., description="Timestamp tạo phiên.")
    updated_at: float = Field(..., description="Timestamp cập nhật lần cuối.")