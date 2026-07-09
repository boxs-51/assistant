from abc import ABC, abstractmethod
from typing import Optional, Any, List, Tuple

from ..models.chroma.base import CacheEntry, CacheGetResult

class VectorStorageDriver( ABC):
    """
    Interface (hợp đồng) trừu tượng cho mọi Vector Storage Driver (Chroma, Qdrant, Pinecone...).
    """

    @abstractmethod
    async def get(self, embedding: list[float]) -> CacheGetResult:
        """
        Tìm kiếm một entry trong vector store dựa trên vector embedding.
        """
        pass

    @abstractmethod
    async def set(self, entry: CacheEntry):
        """Lưu một CacheEntry mới vào vector store."""
        pass

    @abstractmethod
    async def batch_set(self, entries: List[CacheEntry]):
        """Lưu một loạt các CacheEntry mới vào vector store."""
        pass

    @abstractmethod
    async def delete(self, entry_id: str):
        """Xóa một entry khỏi vector store dựa trên ID."""
        pass