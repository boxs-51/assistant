from abc import ABC, abstractmethod
from typing import Optional, Tuple, List, Literal

from .models import CacheEntry

CacheMissReason = Literal["not_found", "expired", "below_threshold", "unknown", "backend_error"]
CacheGetResult = Tuple[Optional[CacheEntry], Optional[float], Optional[CacheMissReason]]

class BaseCacheBackend(ABC):
    """
    Interface (hợp đồng) trừu tượng cho mọi backend của Semantic Cache.
    Bất kỳ backend nào (Chroma, Qdrant, Milvus) đều phải triển khai các phương thức này.
    """

    @abstractmethod
    async def get(self, embedding: list[float]) -> CacheGetResult:
        """
        Tìm kiếm một entry trong cache dựa trên vector embedding.

        Args:
            embedding: Vector embedding của prompt.

        Returns:
            Một tuple chứa (CacheEntry, distance, miss_reason).
            - Khi hit: (entry, distance, None)
            - Khi miss: (None, distance, reason) hoặc (None, None, reason)
        """
        pass

    @abstractmethod
    async def set(self, entry: CacheEntry):
        """Lưu một CacheEntry mới vào backend."""
        pass

    @abstractmethod
    async def batch_set(self, entries: List[CacheEntry]):
        """Lưu một loạt các CacheEntry mới vào backend."""
        pass

    @abstractmethod
    async def delete(self, entry_id: str):
        """Xóa một entry khỏi cache dựa trên ID."""
        pass

    @abstractmethod
    async def cleanup(self):
        """Chạy tác vụ dọn dẹp, ví dụ xóa các entry đã hết hạn."""
        pass

    @abstractmethod
    async def health(self) -> bool:
        """
        Kiểm tra tình trạng sức khỏe của cache backend.

        Returns:
            True nếu backend hoạt động, False nếu ngược lại.
        """
        pass

    @abstractmethod
    async def close(self):
        """Đóng các kết nối đến backend (nếu có)."""
        pass