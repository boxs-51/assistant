from enum import Enum, auto

class ProviderCapability(Enum):
    """
    Định nghĩa các năng lực (capabilities) mà một provider có thể hỗ trợ.
    Sử dụng auto() để tự động gán giá trị, giúp dễ dàng thêm mới.
    """
    # Core Capabilities
    TEXT_GENERATION = auto()
    STREAMING = auto()
    VISION = auto()
    TOOL_CALLING = auto()
    JSON_MODE = auto()

    # API-specific Capabilities
    EMBEDDING = auto()
    IMAGE_GENERATION = auto()
    IMAGE_EDIT = auto()
    AUDIO_TRANSCRIPTION = auto()
    TEXT_TO_SPEECH = auto()
    FILE_UPLOAD = auto()
    FILE_DOWNLOAD = auto()
    
    # Advanced Capabilities
    BATCH_PROCESSING = auto()
    STRUCTURED_OUTPUT = auto()
    WEB_SEARCH = auto()