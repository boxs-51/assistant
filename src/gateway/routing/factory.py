from typing import Dict, Type, Optional

from .providers.base.provider import BaseProvider
from .providers.openai import OpenAIProvider
from .providers.ollama import OllamaProvider
from .providers.google import GoogleProvider

class ProviderFactory:
    """
    Tạo các instance của các lớp provider cụ thể.
    Đây là nơi duy nhất trong hệ thống có kiến thức về các lớp triển khai provider.
    """
    _provider_classes: Dict[str, Type[BaseProvider]] = {
        "openai": OpenAIProvider,
        "ollama": OllamaProvider,
        "gemini": GoogleProvider,
    }

    @classmethod
    def create_provider(cls, name: str) -> Optional[BaseProvider]:
        """Tạo một provider instance dựa trên tên."""
        provider_class = cls._provider_classes.get(name)
        if provider_class:
            return provider_class()
        return None