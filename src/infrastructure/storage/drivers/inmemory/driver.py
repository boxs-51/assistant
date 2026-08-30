import asyncio
import time
from typing import Any, Optional

from ....config.schemas import DriverConfig
from ...interfaces.cache import CacheDriver


class InMemoryDriver(CacheDriver):
    """
    In-memory implementation của CacheDriver.

    Phù hợp cho:
    - local development
    - test
    - single-process cache
    - temporary cache
    - rate limiting trong một process
    - event deduplication trong một process

    Không phù hợp cho:
    - distributed cache
    - multi-process shared state
    - multi-instance deployment
    """

    def __init__(self, config: DriverConfig):
        self.config = config 

        self._data: dict[str, Any] = {}
        self._expires: dict[str, float] = {}

        self._lock = asyncio.Lock()

        self._connected = False

    async def connect(self) -> None:
        """
        Khởi tạo in-memory cache.

        Không có network connection,
        nhưng vẫn giữ lifecycle giống RedisDriver.
        """

        if self._connected:
            return

        self._connected = True

    async def disconnect(self) -> None:
        """
        Đóng in-memory cache.

        Data được giải phóng khi disconnect.
        """

        async with self._lock:
            self._data.clear()
            self._expires.clear()

        self._connected = False

    async def ping(self) -> bool:
        """
        In-memory backend luôn available nếu đã connect.
        """

        self._require_connected()
        return True

    @property
    def is_connected(self) -> bool:
        """
        Lifecycle state.

        Không phải health check realtime.
        """

        return self._connected

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError(
                "InMemoryDriver is not connected"
            )

    def _is_expired(self, key: str, now: float) -> bool:
        expires_at = self._expires.get(key)

        if expires_at is None:
            return False

        return now >= expires_at

    def _delete_unlocked(self, key: str) -> None:
        self._data.pop(key, None)
        self._expires.pop(key, None)

    async def get(self, key: str) -> Optional[Any]:
        self._require_connected()

        async with self._lock:
            now = time.monotonic()

            if key not in self._data:
                return None

            if self._is_expired(key, now):
                self._delete_unlocked(key)
                return None

            return self._data[key]

    async def set(
        self,
        key: str,
        value: Any,
        expire: Optional[int] = None,
    ) -> None:
        self._require_connected()

        if expire is not None and expire <= 0:
            async with self._lock:
                self._delete_unlocked(key)

            return

        async with self._lock:
            self._data[key] = value

            if expire is None:
                self._expires.pop(key, None)

            else:
                self._expires[key] = (
                    time.monotonic() + expire
                )

    async def delete(self, key: str) -> None:
        self._require_connected()

        async with self._lock:
            self._delete_unlocked(key)

    async def exists(self, key: str) -> bool:
        return key in self._data