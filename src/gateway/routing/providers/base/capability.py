from __future__ import annotations
from enum import Enum, auto
from typing import TYPE_CHECKING

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