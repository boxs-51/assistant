import structlog

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

        if self.config is not None:
            priority = self.config.priority
            mock_enabled = self.config.mock_enabled

            if mock_enabled and priority == ["mock"]:
                provider_instance = ProviderFactory.create_provider("mock")
                if provider_instance:
                    self.registry.register(provider_instance)
                logger.info("Strict offline mock-only discovery enabled")
                return

        for name, provider_class in ProviderFactory._provider_classes.items():
            if not provider_class.is_configured():
                logger.info("Provider skipped because it is not configured", provider=name)
                continue
            provider_instance = ProviderFactory.create_provider(name)
            if provider_instance:
                self.registry.register(provider_instance)
