from typing import Dict, Optional
import structlog

from .providers.base.provider.provider import BaseProvider
from .models import ProviderEntry

logger = structlog.get_logger(__name__)

class ProviderRegistry:
    """
    REFACTORED: Kho chứa (repository) thuần túy để quản lý các ProviderEntry.
    Hoàn toàn không biết về settings hay các lớp provider cụ thể.
    """
    def __init__(self):
        self._entries: Dict[str, ProviderEntry] = {}

    def register(self, provider: BaseProvider):
        """Đăng ký một provider mới, bọc nó trong một ProviderEntry."""
        if provider.name in self._entries:
            logger.warning("Provider already registered. Overwriting.", provider=provider.name)
        
        entry = ProviderEntry(provider=provider)
        self._entries[provider.name] = entry
        logger.info("Provider registered successfully", provider=provider.name)

    def get_provider(self, name: str) -> Optional[BaseProvider]:
        """Lấy một instance provider dựa trên tên."""
        entry = self._entries.get(name)
        return entry.provider if entry else None

    def get_entry(self, name: str) -> Optional[ProviderEntry]:
        """Lấy toàn bộ ProviderEntry dựa trên tên."""
        return self._entries.get(name)

    def list_all_providers(self) -> Dict[str, BaseProvider]:
        """Trả về một dictionary chứa tất cả các instance provider đã đăng ký."""
        return {name: entry.provider for name, entry in self._entries.items()}