from enum import Enum, auto
from typing import Optional, Literal
from .base import GatewayBaseModel

class ProviderCapability(Enum):
    """
    Định nghĩa các năng lực (capabilities) mà một NHÀ CUNG CẤP (provider) hỗ trợ,
    không phụ thuộc vào model cụ thể.
    """
    # =========================
    # API-level features
    # =========================
    BATCH_API = auto()      # Hỗ trợ batch processing API (e.g., OpenAI Batch API)
    FINE_TUNING = auto()    # Hỗ trợ fine-tuning API
    FILES = auto()          # Hỗ trợ file management API (upload, download, etc.)
    ASSISTANTS = auto()     # Hỗ trợ Assistants API

class ProviderInfo(GatewayBaseModel):
    """Thông tin chi tiết về Provider tại tầng Routing."""
    provider_id: str
    display_name: str
    api_base: Optional[str] = None
    status: Literal["active", "degraded", "maintenance"] = "active"