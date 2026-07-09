from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class Identity(BaseModel):
    """
    Đối tượng chứa thông tin định danh của chủ thể sau khi xác thực.
    Đây là "Single Source of Truth" cho các tầng phía sau.
    """
    # ID chính
    user_id: Optional[str] = None
    organization_id: Optional[str] = None
    application_id: Optional[str] = None
    api_key_id: Optional[str] = None

    # Loại xác thực
    auth_type: Literal["jwt", "api_key"]

    # Thông tin về gói dịch vụ và quyền
    plan: str = "free"
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)

    def get_rate_limit_key(self) -> str:
        """
        Lấy khóa định danh duy nhất để áp dụng rate limiting.
        Ưu tiên theo thứ tự: api_key > organization > user.
        """
        return self.api_key_id or self.organization_id or self.user_id or "anonymous"

    class Config:
        frozen = True # Immutable, đảm bảo identity không bị thay đổi sau khi tạo

