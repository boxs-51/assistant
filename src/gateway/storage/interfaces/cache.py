from abc import ABC, abstractmethod
from typing import Any, Optional

class CacheDriver(ABC):
    """
    Interface trừu tượng cho tất cả các driver cache (ví dụ: Redis).
    """

    @abstractmethod
    async def connect(self):
        """Khởi tạo và kiểm tra kết nối đến cache server."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Đóng kết nối đến cache server."""
        pass

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """Lấy giá trị từ cache bằng key."""
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, expire: Optional[int] = None):
        """Lưu một cặp key-value vào cache, có thể có thời gian hết hạn."""
        pass

    @abstractmethod
    async def delete(self, key: str):
        """Xóa một key khỏi cache."""
        pass