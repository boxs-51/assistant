from typing import List
import structlog

from ..config import settings
from .factory import ProviderFactory
from .registry import ProviderRegistry

logger = structlog.get_logger(__name__)

class ProviderDiscovery:
    """
    Tự động khám phá các provider dựa trên cấu hình hệ thống
    và đăng ký chúng vào ProviderRegistry.
    """
    def __init__(self, registry: ProviderRegistry):
        self.registry = registry

    def _is_configured(self, provider_name: str) -> bool:
        """Kiểm tra xem một provider có được cấu hình trong settings hay không."""
        if provider_name == "openai": return bool(settings.OPENAI_API_KEY)
        if provider_name == "ollama": return bool(settings.OLLAMA_BASE_URL)
        if provider_name == "gemini": return bool(settings.GEMINI_API_KEY)
        return False

    def run(self):
        """Chạy quá trình khám phá và đăng ký."""
        logger.info("Starting provider discovery...")
        for name in ProviderFactory._provider_classes.keys():
            if self._is_configured(name):
                provider_instance = ProviderFactory.create_provider(name)
                if provider_instance:
                    self.registry.register(provider_instance)