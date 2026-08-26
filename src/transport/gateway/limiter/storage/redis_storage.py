import os

from typing import Any
from typing import Tuple, Dict, Any
from redis.commands.core import Script
import redis.asyncio as redis
import structlog

from .base import BaseStorage
from .....infrastructure.storage.interfaces.cache import CacheDriver

logger = structlog.get_logger(__name__)


class RedisStorage(BaseStorage):
    """
    Triển khai Storage sử dụng CacheDriver.

    Redis-specific connection/client lifecycle được giữ bên
    infrastructure/storage/drivers/redis.
    """

    def __init__(self, cache_driver: CacheDriver):
        self.cache_driver = cache_driver

        # Chỉ đọc source ở constructor.
        # Không thực hiện network I/O tại đây.
        self.scripts: dict[str, str] = {
            "token_bucket": self._load_lua_script("token_bucket.lua"),
            "sliding_window": self._load_lua_script("sliding_window.lua"),
        }


    def _load_lua_script(self, filename: str) -> str:
        """Đọc Lua source từ filesystem, không truy cập Redis."""
        script_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "scripts",
            filename,
        )
        logger.debug("Loading Lua script", path=script_path)

        try:
            with open(script_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logger.error("Lua script file not found.", path=script_path)
            raise
        except OSError as e:
            logger.critical("Failed to load or register Lua script.", path=script_path, error=str(e))
            raise RuntimeError(f"Could not load Lua script {filename}") from e

    async def execute(self, script_name: str, keys: list, args: list) -> Any:
        """
        Thực thi một Lua script thông qua CacheDriver.
        """
        script = self.scripts.get(script_name)
        if script is None:
            raise ValueError(f"Script '{script_name}' not found or not loaded.")

        return await self.cache_driver.execute_script(
            script=script,
            keys=keys,
            args=args,
        )