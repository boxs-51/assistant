from abc import ABC, abstractmethod
from typing import Any

class VideoProvider(ABC):
    # =========================
    # Video
    # =========================
    @abstractmethod
    async def video_generation(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def video_understanding(self, **kwargs) -> Any: raise NotImplementedError