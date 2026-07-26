from typing import Dict, Any
import yaml
import os
import structlog

from ..base import BaseConfigSource

logger = structlog.get_logger(__name__)

class YamlLoader(BaseConfigSource):
    def __init__(self, file_path: str):
        self.file_path = file_path

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.file_path):
            logger.debug("YAML config file not found, skipping.", path=self.file_path)
            return {}
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except (yaml.YAMLError, IOError) as e:
            logger.error("Failed to load or parse YAML file.", path=self.file_path, error=str(e))
            raise