import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr


class User(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    email: EmailStr
    hashed_password: str
    full_name: Optional[str] = None
    
    organization_id: Optional[uuid.UUID] = None
    
    is_active: bool = True
    is_superuser: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True