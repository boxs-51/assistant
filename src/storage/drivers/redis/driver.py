import structlog
import redis.asyncio as redis
from typing import Any, Optional, Dict

from ...interfaces.cache import CacheDriver

logger = structlog.get_logger(__name__)

class RedisDriver(CacheDriver):
    """Implementation của CacheDriver cho Redis."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.url = config.get("url")
        if not self.url:
            raise ValueError("Redis config must contain a 'url'")
        self._client: Optional[redis.Redis] = None

    async def connect(self):
        """Khởi tạo và kiểm tra kết nối đến Redis."""
        logger.info("Connecting to Redis...", url=self.url)
        self._client = redis.from_url(self.url, decode_responses=True)
        try:
            await self._client.ping()
            logger.info("Redis connection successful.")
        except Exception as e:
            logger.error("Failed to connect to Redis", error=str(e))
            #raise
            pass

    async def disconnect(self):
        """Đóng kết nối Redis."""
        if self._client:
            await self._client.close()
            logger.info("Redis connection closed.")

    async def get(self, key: str) -> Optional[Any]:
        return await self._client.get(key)

    async def set(self, key: str, value: Any, expire: Optional[int] = None):
        await self._client.set(key, value, ex=expire)

    async def delete(self, key: str):
        await self._client.delete(key)
