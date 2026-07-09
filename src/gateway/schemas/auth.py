from pydantic import BaseModel, EmailStr, Field
from typing import List

class UserCreateSchema(BaseModel):
    email: EmailStr
    password: str

class LoginRequestSchema(BaseModel):
    email: EmailStr
    password: str

class TokenSchema(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequestSchema(BaseModel):
    refresh_token: str

class AccessTokenSchema(BaseModel):
    access_token: str
    token_type: str = "bearer"

class OAuthUserInfoSchema(BaseModel):
    email: EmailStr
    provider_user_id: str = Field(..., description="ID của người dùng trên hệ thống của nhà cung cấp OAuth")
    # Có thể thêm các trường khác như name, avatar_url... nếu cần

class UserMeSchema(BaseModel):
    id: str
    email: EmailStr
    roles: List[str]

class APIKeyCreateSchema(BaseModel):
    name: str = Field(..., description="Tên gợi nhớ cho API key, sẽ được dùng để tạo Application nếu chưa có.")

class APIKeyInfoSchema(BaseModel):
    id: str
    name: str = Field(..., description="Tên của Application mà key này thuộc về.")
    prefix: str
    created_at: str
    status: str

class APIKeyResponseSchema(BaseModel):
    id: str
    full_key: str = Field(..., description="API key đầy đủ. Chỉ hiển thị một lần duy nhất.")
    prefix: str
    created_at: str

class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str