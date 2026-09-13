from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from datetime import datetime
import uuid

class BaseMessage(BaseModel):
    """Gốc của mọi thông điệp trong hệ thống."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    correlation_id: str = Field(default_factory=lambda: f"corr_{uuid.uuid4().hex[:12]}")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"frozen": True}

class RuntimeCommand(BaseMessage):
    """Command: Yêu cầu thực hiện một hành động (Chỉ có 1 Handler nhận)."""
    command_id: str = Field(default_factory=lambda: f"cmd_{uuid.uuid4().hex[:12]}")
    command_type: str
    session_id: str
    user_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)

class RuntimeEvent(BaseMessage):
    """Event: Thông báo một sự kiện đã xảy ra (Nhiều Subscribers)."""
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    event_type: str
    session_id: str
    user_id: str
    causation_id: Optional[str] = None  # ID của Command hoặc Event sinh ra nó
    project_id: Optional[str] = None
    workspace_id: Optional[str] = None
    priority: int = 0
    payload: Dict[str, Any] = Field(default_factory=dict)