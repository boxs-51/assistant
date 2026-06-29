from enum import Enum, auto

class ProviderCapability(Enum):
    """
    Định nghĩa các năng lực (capabilities) mà một provider có thể hỗ trợ.
    Sử dụng auto() để tự động gán giá trị, giúp dễ dàng thêm mới.
    """
    TEXT_GENERATION = auto()
    STREAMING = auto()
    VISION = auto()
    TOOL_CALLING = auto()