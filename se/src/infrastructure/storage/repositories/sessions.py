import structlog
from typing import Optional
from redis.exceptions import RedisError

from ..interfaces.repository import BaseRepository
from ..interfaces.cache import CacheDriver

logger = structlog.get_logger(__name__)

class SessionRepository(BaseRepository):
    """
    Repository để quản lý session và refresh tokens sử dụng Cache (Redis).
    """
    def __init__(self, cache_driver: CacheDriver):
        self.cache_driver = cache_driver
        # Định nghĩa prefix để tránh xung đột key trong Redis
        self.prefix = "session:refresh_token:"

    async def save_token(self, user_id: str, token_hash: str, expires_in_seconds: int):
        """
        Lưu hash của refresh token vào cache với user_id làm value.
        Key sẽ là `session:refresh_token:<token_hash>`.
        """
        key = f"{self.prefix}{token_hash}"
        try:
            await self.cache_driver.set(key, user_id, expire=expires_in_seconds)
            logger.debug("Refresh token saved to cache", key=key, user_id=user_id)
        except (RedisError, RuntimeError) as e:
            # Bắt lỗi khi không có Redis, ghi log và cho qua để không làm sập luồng login
            logger.warning("Redis is not available. Skipping token storage.", error=str(e))

    async def get_user_id_by_token(self, token_hash: str) -> Optional[str]:
        """
        Lấy user_id từ cache bằng hash của refresh token.
        """
        key = f"{self.prefix}{token_hash}"
        try:
            return await self.cache_driver.get(key)
        except (RedisError, RuntimeError) as e:
            logger.warning("Redis is not available. Cannot fetch token.", error=str(e))
            return None  # Trả về None tạm thời

    async def delete_token(self, token_hash: str) -> int:
        """Return the atomic cache deletion count; never invent a revoke ACK."""
        key = f"{self.prefix}{token_hash}"
        try:
            deleted = await self.cache_driver.delete(key)
            # Reject void, boolean and unexpected counts from nonconforming
            # drivers rather than treating a no-op as successful revocation.
            if type(deleted) is not int or deleted not in (0, 1):
                raise RuntimeError("Refresh token cache deletion was not confirmed")
            if deleted:
                logger.debug("Refresh token deleted from cache", key=key)
            return deleted
        except (RedisError, RuntimeError) as e:
            logger.warning("Refresh token cache deletion failed", error=str(e))
            raise  # P0A: backend failures must propagate, never HTTP 204.