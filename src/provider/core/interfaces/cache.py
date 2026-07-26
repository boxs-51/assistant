from abc import ABC, abstractmethod
from typing import Any

class CacheProvider(ABC):
    # =========================
    # Cache
    # =========================
    @abstractmethod
    async def create_cache(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def list_cache(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def get_cache(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def delete_cache(self, **kwargs) -> Any: raise NotImplementedError