from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from jose import JWTError, jwt

from ..config import ConfigSchema
# Các cấu hình này nên được đưa vào file settings
class JwtHelper:
    def __init__(self, config: ConfigSchema):
        
        self.config = config.auth
        self.SECRET_KEY = self.config.jwt_secret_key
        self.ALGORITHM = self.config.jwt_algorithm
        self.ACCESS_TOKEN_EXPIRE_MINUTES = self.config.access_token_expire_minutes
        self.REFRESH_TOKEN_EXPIRE_DAYS = self.config.refresh_token_expire_days

    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Tạo một access token mới."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=self.ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire, "type": "access"})
        encoded_jwt = jwt.encode(to_encode, self.SECRET_KEY, algorithm=self.ALGORITHM)
        return encoded_jwt

    def create_refresh_token(self, data: dict) -> str:
        """Tạo một refresh token mới."""
        to_encode = data.copy()
        expire = datetime.now(timezone.utc) + timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS)
        to_encode.update({"exp": expire, "type": "refresh"})
        encoded_jwt = jwt.encode(to_encode, self.SECRET_KEY, algorithm=self.ALGORITHM)
        return encoded_jwt

    def decode_token(self, token: str) -> Optional[dict]:
        """Giải mã một token."""
        try:
            payload = jwt.decode(token, self.SECRET_KEY, algorithms=[self.ALGORITHM])
            return payload
        except JWTError:
            return None