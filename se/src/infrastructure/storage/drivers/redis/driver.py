import hashlib
import structlog
import redis.asyncio as redis
from redis.exceptions import RedisError, NoScriptError
from typing import Any, Optional, Dict

from ....config.schemas import DriverConfig
from ...interfaces.cache import CacheDriver, CacheWriteNotDispatched, CacheWriteCollision

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

        # A connection URL may contain passwords; never include it in logs.
        logger.info("Connecting to Redis...")

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
                # Cleanup exceptions may also contain the configured URL.
                logger.debug("Failed to close Redis client after connection failure")

            self._client = None
            self._connected = False

            logger.error(
                "Failed to connect to Redis",
                error_type=type(e).__name__,
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
        # This state is known before command submission. For any attempted
        # network write, including timeout or lost ACK, do NOT label it as
        # non-dispatched: the backend may already hold a live OTP.
        if self._client is None:
            raise CacheWriteNotDispatched("RedisDriver is not connected; SET was not dispatched before client initialization")
        client = self._require_client()

        try:
            # A previously deployed/unfenced writer can leave a live OTP
            # without the modern PG17 issuance reservation. An atomic SET NX
            # is essential: GET then SET is a TOCTOU overwrite hazard.
            # Do not alter ordinary cache/cooldown set semantics.
            one_time_challenge = key.startswith(
                ("pending_reg:", "pending_password_reset:")
            )
            receipt = await client.set(
                key, value, ex=expire, nx=one_time_challenge
            )
            if one_time_challenge and receipt is None:
                # Redis NX denied the write: previous challenge still live.
                # This is NOT a transport failure that permits SQL fallback.
                raise CacheWriteCollision("Live OTP already exists; issuance denied")
            if receipt is not True:
                # A missing/false/invalid SET reply is never a confirmed OTP.
                # This is still post-dispatch uncertainty, not SQL fallback.
                raise RuntimeError("Redis SET returned an unconfirmed write receipt")

        except RedisError:
            self._connected = False
            raise

    async def delete(self, key: str) -> int:
        client = self._require_client()

        try:
            # Redis DEL is atomic and returns the number of removed keys.
            # Do not discard this receipt: a no-op must not ACK revocation.
            deleted = await client.delete(key)
            # Never coerce True, None, a float or a string to an affected count.
            if type(deleted) is not int:
                raise RuntimeError("Redis DEL returned an unconfirmed deletion count")
            return deleted

        except RedisError:
            self._connected = False
            raise

    async def consume_one_time(self, key: str) -> Optional[Any]:
        """Atomic Redis GETDEL; no separate stale GET or unsafe fallback.

        Requires Redis >= 6.2. Unsupported commands, timeouts and uncertain
        replies propagate as errors, never as a confirmed cache miss.
        """
        client = self._require_client()

        try:
            value = await client.getdel(key)
            # connect() uses decode_responses=True. A boolean, integer or
            # other malformed reply is not proof of one-use consumption.
            if value is not None and type(value) is not str:
                raise RuntimeError("Redis GETDEL returned an unconfirmed atomic receipt")
            self._connected = True
            return value

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