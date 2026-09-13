import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class APIKey(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    key_prefix: str = Field(..., description="Phần tiền tố của API key để hiển thị (ví dụ: 'sk-aBc...XyZ')")
    hashed_key: str = Field(..., description="Giá trị hash của API key đầy đủ, dùng để xác thực")
    
    user_id: uuid.UUID
    organization_id: Optional[uuid.UUID] = None
    
    name: Optional[str] = None
    scopes: List[str] = Field(default_factory=list, description="Phạm vi quyền hạn của key")
    
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    is_active: bool = True

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True