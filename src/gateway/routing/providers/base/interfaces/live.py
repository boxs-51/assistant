from abc import ABC, abstractmethod
from typing import Any

class BaseProvider(ABC):
    # =========================
    # Live API
    # =========================
    @abstractmethod
    async def live(self, **kwargs) -> Any: raise NotImplementedError