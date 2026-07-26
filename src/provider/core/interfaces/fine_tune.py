from abc import ABC, abstractmethod
from typing import Any

class FineTuneProvider(ABC):
    # =========================
    # Fine Tune
    # =========================
    @abstractmethod
    async def fine_tune(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def list_fine_tunes(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def fine_tune_status(self, **kwargs) -> Any: raise NotImplementedError