from enum import Enum

class ApiType(Enum):
    """
    Định nghĩa các loại API endpoint được chuẩn hóa trong Gateway.
    Sử dụng Enum giúp tránh lỗi gõ sai chuỗi và cho phép IDE autocomplete.
    """
    # Core LLM APIs
    CHAT_COMPLETIONS = "chat/completions"
    EMBEDDINGS = "embeddings"
    
    # Image APIs
    IMAGE_GENERATION = "images/generations"
    
    # File APIs
    FILES = "files"
    
    # Management APIs
    MODELS = "models"
