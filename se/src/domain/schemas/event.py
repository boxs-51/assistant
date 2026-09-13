from pydantic import Field
from typing import Any, Dict, Optional
import time
import uuid

from .base import GatewayBaseModel

class BaseEvent(GatewayBaseModel):
    """Schema cơ sở cho mọi sự kiện trong hệ thống Event Bus."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_name: str = Field(..., description="Tên định danh của sự kiện, ví dụ: 'user.created'.")
    session_id: Optional[str] = Field(..., description="")
    timestamp: float = Field(default_factory=time.time)
    payload: Dict[str, Any] = Field(default_factory=dict, description="Dữ liệu đi kèm với sự kiện.")
