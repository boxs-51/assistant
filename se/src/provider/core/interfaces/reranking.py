from abc import ABC, abstractmethod
from typing import Any

class RerankingProvider(ABC):
    # =========================
    # Reranking
    # =========================
    @abstractmethod
    async def rerank(self, **kwargs) -> Any: raise NotImplementedError