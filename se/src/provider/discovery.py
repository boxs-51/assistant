import structlog
import os

from .factory import ProviderFactory
from .registry import ProviderRegistry
from ..infrastructure.config.schemas import ProviderSettings

logger = structlog.get_logger(__name__)


class ProviderDiscovery:
    """Discover providers without instantiating disabled/offline providers."""

    def __init__(self, registry: ProviderRegistry, config: ProviderSettings = None):
        self.registry = registry
        self.config = config

    def run(self):
        logger.info("Starting provider discovery...")
        priority = self.config.priority or []
        for name, provider_class in ProviderFactory._provider_classes.items():

            # 1. Bỏ qua nếu provider không nằm trong danh sách priority
            if priority and name not in priority:
                logger.info("Provider skipped because it is not in priority list", provider=name)
                continue

            # 2. Lấy config riêng của provider này từ ProviderSettings.configs
            provider_config = self.config.configs.get(name) if self.config else None

            # 3. Bỏ qua nếu provider bị disabled trong file config
            if provider_config and not provider_config.enabled:
                logger.info("Provider skipped because it is disabled", provider=name)
                continue

            # 4. Tạo instance và truyền config vào
            try:
                provider_instance = ProviderFactory.create_provider(name, config=provider_config)
                if provider_instance and provider_instance.is_configured():
                    self.registry.register(provider_instance)
                else:
                    logger.info("Provider skipped because it is not configured", provider=name)
            except Exception as e:
                logger.error("Failed to instantiate provider", provider=name, error=str(e))
