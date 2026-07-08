from abc import ABC, abstractmethod
from typing import Any

class AudioProvider(ABC):
   # =========================
    # Audio
    # =========================
    @abstractmethod
    async def speech_to_text(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def text_to_speech(self, **kwargs) -> Any: raise NotImplementedError
