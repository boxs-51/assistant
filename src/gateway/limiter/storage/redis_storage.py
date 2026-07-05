import os
from typing import Tuple, Dict, Any
from redis.commands.core import Script
import redis.asyncio as redis
import structlog

from .base import BaseStorage

logger = structlog.get_logger(__name__)


class RedisStorage(BaseStorage):
    """Triển khai Storage sử dụng Redis và Lua script để đảm bảo tính nguyên tử."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.scripts: Dict[str, Script] = {
            "token_bucket": self._load_lua_script('token_bucket.lua'),
            "sliding_window": self._load_lua_script('sliding_window.lua'),
        }
        logger.info("All rate limiter Lua scripts loaded successfully.")

    def _load_lua_script(self, filename: str) -> "redis.client.Script":
        """Tải một Lua script từ file và đăng ký nó với Redis."""
        script_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', filename)
        logger.debug("Loading Lua script", path=script_path)
        try:
            with open(script_path, 'r', encoding="utf-8") as f:
                script_code = f.read()
            return self.redis.register_script(script_code)
        except FileNotFoundError:
            logger.error("Lua script file not found.", path=script_path)
            raise
        except Exception as e:
            logger.critical("Failed to load or register Lua script.", path=script_path, error=str(e))
            raise RuntimeError(f"Could not load Lua script {filename}") from e

    async def execute(self, script_name: str, keys: list, args: list) -> Any:
        """
        Thực thi một script đã được đăng ký với các key và argument đã cho.
        """
        script = self.scripts.get(script_name)
        if not script:
            raise ValueError(f"Script '{script_name}' not found or not loaded.")
        return await script(keys=keys, args=args)