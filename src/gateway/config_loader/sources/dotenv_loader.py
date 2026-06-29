from typing import Dict, Any
from dotenv import dotenv_values
import os
import structlog

from .base import BaseConfigSource
from .utils import parse_nested_keys

logger = structlog.get_logger(__name__)

class DotEnvLoader(BaseConfigSource):
    def __init__(self, dotenv_path: str = ".env"):
        self.dotenv_path = dotenv_path

    def load(self) -> Dict[str, Any]:
        """
        Tải cấu hình từ file .env và chuyển đổi các key.
        Ví dụ: `GATEWAY__PORT=8001` -> `{"gateway": {"port": 8001}}`
        """
        if not os.path.exists(self.dotenv_path):
            logger.debug(".env file not found, skipping.", path=self.dotenv_path)
            return {}

        try:
            # Sử dụng dotenv_values để không làm ảnh hưởng đến os.environ
            env_values = dotenv_values(self.dotenv_path)
            # Chỉ xử lý các key không rỗng
            raw_config = {k: v for k, v in env_values.items() if v is not None}
            return parse_nested_keys(raw_config)
        except Exception as e:
            logger.error("Failed to load .env file.", path=self.dotenv_path, error=str(e))
            return {}