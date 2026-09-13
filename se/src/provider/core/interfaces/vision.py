from abc import ABC, abstractmethod
from typing import Any

class OCRProvider(ABC):
    # =========================
    # OCR / Vision
    # =========================
    @abstractmethod
    async def vision(self, **kwargs) -> Any: raise NotImplementedError