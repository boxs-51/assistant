from abc import ABC, abstractmethod
from typing import Any

class ModelProvider(ABC):
    # ========================
    # Models
    # =========================
    @abstractmethod
    async def models(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def model(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def model_capabilities(self, **kwargs) -> Any: raise NotImplementedError