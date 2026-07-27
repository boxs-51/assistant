from enum import Enum

# =================================================================
# 0. ENUMS (Định nghĩa các loại hằng số)
# =================================================================
class ToolType(str, Enum):
    """Phân loại Tool độc lập để ExecutorRegistry điều phối chính xác."""
    LOCAL = "LOCAL"
    MCP = "MCP"
    NATIVE = "NATIVE"
    WORKFLOW = "WORKFLOW"
    
class MessageContentType(str, Enum):
    """Các loại nội dung có thể có trong một phần của message."""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    TOOL_RESULT = "tool_result"
    URL = "url"

class FinishReason(str, Enum):
    """Lý do kết thúc một lượt sinh câu trả lời, được chuẩn hóa từ nhiều provider."""
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    RECITATION = "recitation" # Gemini
    SAFETY = "safety" # Gemini
    END_TURN = "end_turn" # Claude
    MAX_TOKENS = "max_tokens" # Claude
    TOOL_USE = "tool_use" # Claude
    UNKNOWN = "unknown"



