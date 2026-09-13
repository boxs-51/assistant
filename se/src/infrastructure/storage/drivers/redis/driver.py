import hashlib
import structlog
import redis.asyncio as redis
from redis.exceptions import RedisError, NoScriptError
from typing import Any, Optional, Dict

from ....config.schemas import DriverConfig
from ...interfaces.cache import CacheDriver

logger = structlog.get_logger(__name__)


class RedisDriver(CacheDriver):
    """Implementation của CacheDriver cho Redis."""

    def __init__(self, config: DriverConfig):
        self.config = config
        self.url = config.options.get("url", "redis://localhost:6379/0")
        self._client: Optional[redis.Redis] = None
        self._connected = False

    async def connect(self) -> None:
        """
        Khởi tạo và kiểm tra kết nối đến Redis.

        Không retry vô hạn tại đây.
        """

        logger.info("Connecting to Redis...", url=self.url)

        if self._client is not None:
            await self.disconnect()

        client = redis.from_url(
            self.url,
            decode_responses=True,
        )

        try:
            await client.ping()

        except Exception as e:
            try:
                await client.aclose()
            except Exception:
                logger.debug(
                    "Failed to close Redis client after connection failure",
                    exc_info=True,
                )

            self._client = None
            self._connected = False

            logger.error(
                "Failed to connect to Redis",
                error=str(e),
            )

            raise

        self._client = client
        self._connected = True

        logger.info("Redis connection successful.")

    async def disconnect(self) -> None:
        """Đóng kết nối Redis một cách idempotent."""

        client = self._client

        self._client = None
        self._connected = False

        if client is not None:
            await client.aclose()
            logger.info("Redis connection closed.")

    async def ping(self) -> bool:
        """Kiểm tra Redis có phản hồi hay không."""

        client = self._require_client()

        try:
            result = await client.ping()
            self._connected = True
            return bool(result)

        except RedisError:
            self._connected = False
            raise

    @property
    def is_connected(self) -> bool:
        """
        Trạng thái lifecycle cục bộ.

        Không phải realtime health check.
        """

        return self._connected

    def _require_client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("RedisDriver is not connected")

        return self._client

    async def get(self, key: str) -> Optional[Any]:
        client = self._require_client()

        try:
            return await client.get(key)

        except RedisError:
            self._connected = False
            raise

    async def get_ttl(self, key: str) -> Optional[float]:
        client = self._require_client()

        try:
            pttl = await client.pttl(key)

            # Redis trả về:
            # -2: Key không tồn tại
            # -1: Key không có thời gian hết hạn (persist)
            if pttl < 0:
                return None

            return pttl / 1000.0

        except RedisError:
            self._connected = False
            raise

    async def set(
        self,
        key: str,
        value: Any,
        expire: Optional[int] = None,
    ) -> None:
        client = self._require_client()

        try:
            await client.set(
                key,
                value,
                ex=expire,
            )

        except RedisError:
            self._connected = False
            raise

    async def delete(self, key: str) -> None:
        client = self._require_client()

        try:
            await client.delete(key)

        except RedisError:
            self._connected = False
            raise

    async def execute_script(
        self,
        script: str,
        keys: list[str],
        args: list[Any],
    ) -> Any:
        """
        Thực thi Lua script atomically.

        EVALSHA được ưu tiên.
        Nếu Redis restart và script cache mất, NOSCRIPT sẽ fallback EVAL.

        Không expose Redis Script object ra ngoài infrastructure.
        """

        client = self._require_client()

        sha = hashlib.sha1(
            script.encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()

        try:
            result = await client.evalsha(
                sha,
                len(keys),
                *(keys + args),
            )

            self._connected = True
            return result

        except NoScriptError:
            try:
                result = await client.eval(
                    script,
                    len(keys),
                    *(keys + args),
                )

                self._connected = True
                return result

            except RedisError:
                self._connected = False
                raise

        except RedisError:
            self._connected = False
            raise