import structlog

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

    def run(self):
        """Chạy quá trình khám phá và đăng ký."""
        logger.info("Starting provider discovery...")
        for name, provider_class in ProviderFactory._provider_classes.items():
            if name == "mock" and not provider_class.is_configured():
                provider_instance = ProviderFactory.create_provider(name)
                if provider_instance:
                    self.registry.register(provider_instance)
                continue
            # Ủy quyền việc kiểm tra cấu hình cho chính lớp Provider
            if provider_class.is_configured():
                provider_instance = ProviderFactory.create_provider(name)
                if provider_instance:
                    self.registry.register(provider_instance)