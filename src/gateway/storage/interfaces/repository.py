from abc import ABC

class BaseRepository(ABC):
    """
    Lớp cha trừu tượng cho tất cả các Repository.
    Mỗi repository sẽ chịu trách nhiệm cho một aggregate root (ví dụ: User, APIKey).
    """
    pass