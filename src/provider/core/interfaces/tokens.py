from abc import ABC, abstractmethod
from typing import Any

class TokenProvider(ABC):
    # =========================
    # Tokens
    # =========================
    @abstractmethod
    async def count_tokens(self, **kwargs) -> Any: raise NotImplementedError