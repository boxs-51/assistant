from pydantic import BaseModel, Field
from typing import Optional
import time # type: ignore


class CacheMetadata(BaseModel):
    """
    Lưu trữ các thông tin bổ sung về một cache entry.
    """
    prompt: str
    provider: Optional[str] = None
    model: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    expires_at: Optional[float] = None
    hit_count: int = 0
    # Thêm các trường khác nếu cần
    # token_input: Optional[int] = None
    # token_output: Optional[int] = None
    # latency: Optional[float] = None


class CacheEntry(BaseModel):
    """
    Đại diện cho một đối tượng được lưu trong cache.
    """
    id: str
    response: str
    embedding: list[float]
    metadata: CacheMetadata

    def to_storage_dict(self) -> dict:
        """Chuyển đổi thành dict để lưu vào metadata của ChromaDB."""
        return self.metadata.model_dump()