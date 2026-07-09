from abc import ABC, abstractmethod
from typing import Any
class EmbeddingProvider(ABC):
    # =========================
    # Embeddings
    # =========================
    @abstractmethod
    async def embeddings(self, **kwargs) -> Any: raise NotImplementedError

