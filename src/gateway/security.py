import re
import secrets
from typing import AsyncGenerator, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .config import settings
# Tích hợp GuardrailSystem lõi
from ..guardrail.guar import GuardrailSystem

# Cơ chế xác thực Bearer Token
bearer_scheme = HTTPBearer()

async def authenticate_client(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    """
    Dependency để xác thực client dựa trên Bearer Token.
    So sánh token được cung cấp với API_KEY trong file cấu hình.
    """
    # Nếu tắt xác thực trong config, bỏ qua và trả về một client_id mặc định
    if not settings.security.enable_auth:
        return "anonymous_client"

    # So sánh token
    is_correct_scheme = credentials.scheme == "Bearer"
    is_correct_token = secrets.compare_digest(credentials.credentials, settings.security.api_key)

    if not (is_correct_scheme and is_correct_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Trả về một định danh cho client đã xác thực, có thể dùng cho rate limiting
    return "authenticated_client"

class InputGuardrailAdapter:
    """Adapter để tích hợp Input Guardrail của hệ thống lõi vào Gateway."""
    def __init__(self, guardrail_system: GuardrailSystem):
        self.system = guardrail_system

    def validate(self, text: str) -> bool:
        """Ủy quyền việc kiểm tra cho GuardrailSystem."""
        if not settings.security.enable_input_guardrail:
            return True
        return self.system.validate_input(text)

class OutputGuardrailAdapter:
    """Adapter để tích hợp Output Guardrail của hệ thống lõi vào Gateway."""
    def __init__(self, guardrail_system: GuardrailSystem):
        self.system = guardrail_system

    def sanitize(self, text: str) -> str:
        """Ủy quyền việc làm sạch cho GuardrailSystem."""
        if not settings.security.enable_output_guardrail:
            return text
        return self.system.sanitize_output(text)

    async def sanitize_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """Ủy quyền việc làm sạch stream cho GuardrailSystem."""
        if not settings.security.enable_output_guardrail:
            async for chunk in stream:
                yield chunk
        else:
            async for sanitized_chunk in self.system.sanitize_stream(stream):
                yield sanitized_chunk
