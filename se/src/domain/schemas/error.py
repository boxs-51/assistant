from .base import GatewayBaseModel
from pydantic import Field
from typing import Optional
import time
# =================================================================
# 9. BỔ SUNG GATEWAY ERROR DTO
# =================================================================

class GatewayErrorDetails(GatewayBaseModel):
    message: str
    type: str  # e.g., validation_error, provider_timeout, rate_limit_exceeded
    param: Optional[str] = None
    code: Optional[str] = None

class GatewayError(GatewayBaseModel):
    """DTO chuẩn hóa lỗi trả về cho Client, đóng gói các lỗi từ nhiều Provider khác nhau."""
    error: GatewayErrorDetails
    provider: Optional[str] = None
    timestamp: int = Field(default_factory=lambda: int(time.time()))
    status_code: int