from pydantic import Field
from typing import List, Optional, Dict, Any

from .base import GatewayBaseModel
from .session import Session
from .attachment import GatewayAttachment

class Project(GatewayBaseModel):
    """
    Đại diện cho một Project hoặc không gian làm việc.
    Một Project chứa nhiều Session và có một kho lưu trữ file chung.
    """
    project_id: str = Field(..., description="ID duy nhất của project.")
    user_id: str = Field(..., description="ID của người dùng sở hữu project.")
    organization_id: Optional[str] = Field(None)
    name: str = Field(..., description="Tên của project.")
    sessions: List[Session] = Field(default_factory=list, description="Danh sách các session trong project.")
    files: List[GatewayAttachment] = Field(default_factory=list, description="Kho file dùng chung cho toàn bộ project.")
    created_at: float
    updated_at: float

class ContextObject(GatewayBaseModel):
    """
    Đối tượng ngữ cảnh được tải vào runtime khi một session được kích hoạt.
    Nó chứa tất cả thông tin cần thiết để Agent hoạt động.
    """
    project: Optional[Project] = Field(None, description="Project chứa session này (nếu có).")
    session: Session = Field(..., description="Session đang hoạt động.")
    accessible_files: List[GatewayAttachment] = Field(default_factory=list, description="Tất cả các file mà session này có thể truy cập (bao gồm file của project và file riêng của session).")