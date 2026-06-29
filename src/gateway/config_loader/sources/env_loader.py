from typing import Dict, Any
import os

from .base import BaseConfigSource
from .utils import parse_nested_keys

class EnvLoader(BaseConfigSource):
    def __init__(self, prefix: str = "APP_"):
        self.prefix = prefix

    def load(self) -> Dict[str, Any]:
        """
        Tải cấu hình từ các biến môi trường OS có prefix.
        Ví dụ: `APP_GATEWAY__PORT=8001` -> `{"gateway": {"port": 8001}}`
        """
        raw_config = {}
        for key, value in os.environ.items():
            if key.startswith(self.prefix):
                # Loại bỏ prefix và chuyển thành lowercase
                trimmed_key = key[len(self.prefix):]
                raw_config[trimmed_key] = value
        return parse_nested_keys(raw_config)