from abc import ABC, abstractmethod
from typing import Any, Optional


class CacheDriver(ABC):
    """
    Interface trừu tượng cho các cache driver.

    Application/transport layer không được phụ thuộc vào implementation
    cụ thể như redis.Redis.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Khởi tạo và kiểm tra kết nối đến cache server."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Đóng kết nối đến cache server."""
        pass

    @abstractmethod
    async def ping(self) -> bool:
        """Kiểm tra cache backend có khả dụng hay không."""
        pass

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        """Lấy giá trị từ cache bằng key."""
        pass

    @abstractmethod
    async def get_ttl(self, key: str) -> Optional[float]:
        """
        Lấy thời gian sống còn lại (TTL) của key tính theo giây.

        Returns:
            - float: Số giây còn lại trước khi key hết hạn.
            - None: Nếu key không tồn tại hoặc không thiết lập thời gian hết hạn (persist).
        """
        pass

    @abstractmethod
    async def set(
        self,
        key: str,
        value: Any,
        expire: Optional[int] = None,
    ) -> None:
        """Lưu một cặp key-value vào cache bằng key."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Xóa một key khỏi cache."""
        pass
