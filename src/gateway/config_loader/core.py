from typing import Optional, Dict, Any
from pydantic import ValidationError
import structlog

from .schemas import ConfigSchema
from .exceptions import ConfigValidationError
from .sources.yaml_loader import YamlLoader

logger = structlog.get_logger(__name__)

class ConfigLoader:
    def __init__(self, default_config_path: str = "config/default.yaml"):
        self.default_config_path = default_config_path

    def load_config(self) -> ConfigSchema:
        """
        Tải, hợp nhất, và xác thực cấu hình.
        Trong Giai đoạn 1, chỉ tải từ default.yaml.
        """
        logger.info("Starting configuration loading process...")

        # Tải từ nguồn mặc định
        default_loader = YamlLoader(self.default_config_path)
        config_data = default_loader.load()

        # TODO (Phase 2): Tải và hợp nhất các nguồn khác (env.yaml, .env, os env) ở đây.

        try:
            validated_config = ConfigSchema.model_validate(config_data)
            logger.info("Configuration loaded and validated successfully.")
            return validated_config
        except ValidationError as e:
            logger.critical("Configuration validation failed!", errors=e.errors())
            raise ConfigValidationError(f"Configuration validation failed: {e}") from e

class ConfigurationRegistry:
    _config: Optional[ConfigSchema] = None

    @classmethod
    def get_config(cls) -> ConfigSchema:
        if cls._config is None:
            # Đây là một fallback, lý tưởng là config phải được load lúc startup
            raise RuntimeError("Configuration has not been loaded. Please load it at application startup.")
        return cls._config

    @classmethod
    def set_config(cls, config: ConfigSchema):
        cls._config = config