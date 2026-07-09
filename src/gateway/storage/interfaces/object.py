from abc import ABC, abstractmethod
from typing import Any


class ObjectStorageDriver(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def get_client(self) -> Any: ...