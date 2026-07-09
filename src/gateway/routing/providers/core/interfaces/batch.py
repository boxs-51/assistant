from abc import ABC, abstractmethod
from typing import Any

class BatchProvider(ABC):
    # =========================
    # Batch
    # =========================
    @abstractmethod
    async def create_batch(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def batch_status(self, **kwargs) -> Any: raise NotImplementedError