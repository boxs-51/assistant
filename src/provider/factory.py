from typing import Dict, Type, Optional

from ..infrastructure.config.schemas import ProviderConfig
from .core.provider import BaseProvider
from .openai import OpenAIProvider
from .ollama import OllamaProvider
from .gemini import GeminiProvider
from .mock import MockProvider

class ProviderFactory:
    """
    Tạo các instance của các lớp provider cụ thể.
    Đây là nơi duy nhất trong hệ thống có kiến thức về các lớp triển khai provider.
    """
    _provider_classes: Dict[str, Type[BaseProvider]] = {
        "openai": OpenAIProvider,
        "ollama": OllamaProvider,
        "gemini": GeminiProvider,
        "mock": MockProvider,
    }

    @classmethod
    def create_provider(cls, name: str, config: ProviderConfig) -> Optional[BaseProvider]:
        """Tạo một provider instance dựa trên tên."""
        provider_class = cls._provider_classes.get(name)
        if provider_class:
            return provider_class(config=config)
        return None