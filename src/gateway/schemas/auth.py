from pydantic import BaseModel, EmailStr, Field
from typing import List, Optional, Dict, Any
from pydantic.networks import HttpUrl

# --- AUTHENTICATION SCHEMAS ---

class UserCreateSchema(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Mật khẩu tối thiểu 6 ký tự")

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


# --- OAUTH SCHEMAS ---

class OAuthUserInfoSchema(BaseModel):
    # --- Thông tin bắt buộc ---
    provider: str = Field(..., description="Tên nhà cung cấp (e.g., 'google', 'facebook', 'github')")
    provider_user_id: str = Field(..., description="ID định danh duy nhất của người dùng trên hệ thống OAuth")
    email: EmailStr = Field(..., description="Email của người dùng")
    
    # --- Thông tin cá nhân (Có thể trống tùy user thiết lập công khai hay không) ---
    name: Optional[str] = Field(None, description="Họ và tên đầy đủ")
    given_name: Optional[str] = Field(None, description="Tên (First Name)")
    family_name: Optional[str] = Field(None, description="Họ (Last Name)")
    picture_url: Optional[HttpUrl] = Field(None, description="URL ảnh đại diện (định dạng URL hợp lệ)")
    
    # --- Thông tin bổ sung ---
    email_verified: Optional[bool] = Field(None, description="Trạng thái xác thực email từ nhà cung cấp")
    locale: Optional[str] = Field(None, description="Ngôn ngữ/Vùng quốc gia (e.g., 'vi', 'en-US')")
    profile_url: Optional[HttpUrl] = Field(None, description="Link đến trang cá nhân của user (phổ biến ở GitHub, Facebook)")
    
    # --- "Cú chốt" bảo hiểm ---
    raw_data: Dict[str, Any] = Field(None, description="Lưu toàn bộ JSON thô trả về từ OAuth để phòng hờ")

    class Config:
        frozen = True 

# --- USER & OTP SCHEMAS ---

class UserMeSchema(BaseModel):
    id: str
    email: EmailStr
    roles: List[str] = Field(default_factory=list)

class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6, description="Mã OTP gồm 6 chữ số")


# --- API KEY SCHEMAS ---

class APIKeyCreateSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Tên gợi nhớ cho API key")

class APIKeyInfoSchema(BaseModel):
    id: str
    name: str = Field(..., description="Tên của Application mà key này thuộc về.")
    prefix: str
    created_at: str # Chuyển từ str sang datetime để tự động format chuẩn ISO 8601
    status: str

class APIKeyResponseSchema(BaseModel):
    id: str
    full_key: str = Field(..., description="API key đầy đủ. Chỉ hiển thị một lần duy nhất.")
    prefix: str
    created_at: str # Chuyển từ str sang datetime

    class Config:
        frozen = True 