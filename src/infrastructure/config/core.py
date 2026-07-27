from typing import Optional, Dict, Any, List
from pydantic import ValidationError
import structlog

from .schemas import ConfigSchema
from .exceptions import ConfigValidationError
from .base import BaseConfigSource
from .sources.yaml_loader import YamlLoader
from .sources.dotenv_loader import DotEnvLoader
from .sources.env_loader import EnvLoader
from .merge import deep_merge

logger = structlog.get_logger(__name__)

class ConfigLoader:
    def __init__(self, default_config_path: str = "config/default.yaml"):
        self.default_config_path = default_config_path

    def _get_sources(self) -> List[BaseConfigSource]:
        """
        Định nghĩa các nguồn cấu hình và thứ tự ưu tiên của chúng.
        Nguồn ở cuối danh sách sẽ có độ ưu tiên cao nhất (ghi đè lên các nguồn trước).
        """
        return [
            # 1. Cấu hình mặc định (ưu tiên thấp nhất)
            YamlLoader(self.default_config_path),
            # 2. Cấu hình từ file .env
            DotEnvLoader(),
            # 3. Cấu hình từ biến môi trường OS (ưu tiên cao nhất)
            EnvLoader(prefix="GATEWAY_") # Ví dụ: GATEWAY_OPENAI__API_KEY=...
        ]

    def load_config(self) -> ConfigSchema:
        """
        Tải, hợp nhất, và xác thực cấu hình.
        Thứ tự hợp nhất: default.yaml -> .env -> Biến môi trường OS.
        """
        logger.info("Starting configuration loading process...")
        
        final_config: Dict[str, Any] = {}
        sources = self._get_sources()
        
        for source in sources:
            try:
                source_name = source.__class__.__name__
                logger.debug(f"Loading config from {source_name}...")
                config_data = source.load()
                if config_data:
                    final_config = deep_merge(final_config, config_data)
                    logger.debug(f"Successfully merged config from {source_name}.")
            except Exception as e:
                logger.error(f"Failed to load config from {source_name}", error=str(e))
                # Quyết định có nên dừng lại hay tiếp tục tùy vào yêu cầu
                # Ở đây chúng ta chọn tiếp tục để các nguồn khác có cơ hội tải
        
        try:
            validated_config = ConfigSchema.model_validate(final_config)
            logger.info("Configuration loaded and validated successfully.")
            return validated_config
        except ValidationError as e:
            # Log lỗi validation một cách chi tiết
            logger.critical("Configuration validation failed!", errors=[err for err in e.errors()])
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