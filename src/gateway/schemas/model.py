from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set
from pydantic import  Field
from .base import GatewayBaseModel
import time

class ModelCapability(Enum):
    """
    Định nghĩa các năng lực (capabilities) mà một MODEL cụ thể có thể hỗ trợ.
    Sử dụng auto() để tự động gán giá trị, giúp dễ dàng thêm mới.
    Hệ thống sẽ truy vấn các năng lực này từ provider cho từng model.
    """
    # =========================
    # Chat / Completion
    # =========================
    CHAT = auto()
    CHAT_STREAM = auto()
    CHAT_BATCH = auto()

    # =========================
    # Embeddings
    # =========================
    EMBEDDINGS = auto()
    EMBEDDINGS_BATCH = auto()

    # =========================
    # Images
    # =========================
    IMAGE_GENERATION = auto()
    IMAGE_EDIT = auto()
    IMAGE_VARIATION = auto()

    # =========================
    # Audio
    # =========================
    SPEECH_TO_TEXT = auto()
    SPEECH_TO_TEXT_STREAM = auto()
    TEXT_TO_SPEECH = auto()
    TEXT_TO_SPEECH_STREAM = auto()
    AUDIO_TRANSLATION = auto()

    # =========================
    # Video
    # =========================
    VIDEO_GENERATION = auto()
    VIDEO_UNDERSTANDING = auto()

    # =========================
    # Tokens
    # =========================
    TOKEN_COUNT = auto()
    TOKENIZE = auto()

    # =========================
    # Function / Tool Calling
    # =========================
    TOOL_CALLING = auto()

    # =========================
    # Search
    # =========================
    WEB_SEARCH = auto()

    # =========================
    # Code Execution
    # =========================
    CODE_EXECUTION = auto()

    # =========================
    # Moderation / Safety
    # =========================
    MODERATION = auto()

    # =========================
    # Reranking
    # =========================
    RERANK = auto()

    # =========================
    # Vision / OCR
    # =========================
    VISION = auto() # Khả năng hiểu hình ảnh trong prompt
    OCR = auto()

    # =========================
    # Special Modes
    # =========================
    JSON_MODE = auto() # Hỗ trợ ép đầu ra JSON
    STRUCTURED_OUTPUT = auto() # Hỗ trợ schema phức tạp hơn JSON

# =================================================================
# 4. THIẾT KẾ LẠI MODEL INFO (Nền tảng Routing, Capability, Pricing)
# =================================================================

class ContextLimits(GatewayBaseModel): # Giữ nguyên
    """Định nghĩa cấu trúc cho giới hạn ngữ cảnh của model."""
    context_window: int = Field(..., description="Tổng độ dài context window")
    max_output_tokens: int = Field(..., description="Giới hạn tối đa của output tokens")
    max_input_tokens: Optional[int] = Field(None, description="Giới hạn tối đa của input tokens nếu có")

class ModelInfo(GatewayBaseModel): # Giữ nguyên
    """Cấu trúc lõi quản lý thông tin model phục vụ cho thuật toán Routing và kiểm soát Capability."""
    id: str = Field(..., description="Unique ID của model trong hệ thống Gateway (e.g., 'gpt-4o')")
    object: str = "model"
    display_name: str
    provider: str = Field(..., description="Mã nhà cung cấp gốc (openai, anthropic, azure,...)")
    family: str = Field(..., description="Dòng model (e.g., gpt-4, claude-3, llama-3)")
    version: str
    description: str
    limits: ContextLimits
    capabilities: Set[ModelCapability] = Field(default_factory=set)
    is_active: bool = True
    fallback_model_id: Optional[str] = Field(None, description="ID của model thay thế nếu model này die")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str

class ModelList(GatewayBaseModel): # Giữ nguyên
    object: str = "list"
    data: List[ModelInfo]