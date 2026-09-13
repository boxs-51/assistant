from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Literal, Set

class Identity(BaseModel):
    """
    Đối tượng chứa thông tin định danh của chủ thể sau khi xác thực.
    Đây là "Single Source of Truth" cho các tầng phía sau.
    """
    # === ID chính ===
    user_id: Optional[str] = None
    organization_id: Optional[str] = None
    application_id: Optional[str] = None
    api_key_id: Optional[str] = None
    session_id: Optional[str] = None # ID của session (ví dụ: từ Redis)
    tenant_id: Optional[str] = None  # Dành cho kiến trúc Multi-Tenant

    # === Thông tin request ===
    request_id: Optional[str] = None # ID để trace request end-to-end
    device_id: Optional[str] = None  # ID của thiết bị client

    # Loại xác thực
    auth_type: Literal["jwt", "api_key", "admin_key", "guest"]

    # Thông tin về gói dịch vụ và quyền
    plan: str = "free"
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    scopes: Set[str] = Field(default_factory=set, description="Các quyền truy cập được cấp (vd: 'gmail.read', 'drive.write')")

    def get_rate_limit_key(self) -> str:
        """
        Lấy khóa định danh duy nhất để áp dụng rate limiting.
        Ưu tiên theo thứ tự: api_key > organization > user.
        """
        return self.api_key_id or self.organization_id or self.user_id or "anonymous"

    model_config = ConfigDict(frozen = True)
