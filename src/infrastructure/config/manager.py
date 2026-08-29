from typing import Any, Optional
from .schemas import (
    ConfigSchema, GatewaySettings, AuthenticationSettings, 
    ProviderSettings, OpenAISettings, RedisSettings
)
from .core import ConfigLoader

class ConfigManager:
    _instance: Optional['ConfigManager'] = None
    _config: Optional[ConfigSchema] = None

    def __init__(self, default_config_path: str = "config/default.yaml"):
        self.loader = ConfigLoader(default_config_path=default_config_path)

    @classmethod
    def get_instance(cls, default_config_path: str = "config/default.yaml") -> 'ConfigManager':
        if cls._instance is None:
            cls._instance = cls(default_config_path=default_config_path)
        return cls._instance

    def initialize(self) -> ConfigSchema:
        """Khởi tạo và lưu cấu hình vào bộ nhớ."""
        self._config = self.loader.load_config()
        return self._config

    def reload(self) -> ConfigSchema:
        """Tải lại cấu hình mới (Hot reload)."""
        return self.initialize()

    @property
    def config(self) -> ConfigSchema:
        if self._config is None:
            return self.initialize()
        return self._config

    # --- API Truy cập nhanh bằng Dot-Notation ---
    def get(self, path: str, default: Any = None) -> Any:
        """
        Ví dụ: manager.get("gateway.port", 8000)
               manager.get("openai.api_key")
        """
        keys = path.split(".")
        val: Any = self.config
        for key in keys:
            if isinstance(val, dict):
                val = val.get(key, None)
            elif hasattr(val, key):
                val = getattr(val, key)
            else:
                return default
            if val is None:
                return default
        return val

    # --- API Helper Sub-Configs ---
    def get_gateway(self) -> GatewaySettings: return self.config.gateway
    def get_auth(self) -> AuthenticationSettings: return self.config.auth
    def get_provider(self) -> ProviderSettings: return self.config.provider
    def get_openai(self) -> OpenAISettings: return self.config.openai
    def get_redis(self) -> RedisSettings: return self.config.redis

    # --- Utility Business Helpers ---
    def is_provider_active(self, provider_name: str) -> bool:
        """Kiểm tra provider có nằm trong danh sách priority và đã cấu hình key chưa."""
        if provider_name not in self.config.provider.priority:
            return False
        
        provider_setting = getattr(self.config, provider_name.lower(), None)
        if provider_setting and hasattr(provider_setting, 'api_key'):
            return bool(provider_setting.api_key)
        return True # Dành cho local provider như Ollama không cần api_key