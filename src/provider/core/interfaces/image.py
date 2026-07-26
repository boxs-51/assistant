from abc import ABC, abstractmethod
from typing import Any

class ImageProvider(ABC):
    # =========================
    # Images
    # =========================
    @abstractmethod
    async def image_generation(self, **kwargs) -> Any: raise NotImplementedError