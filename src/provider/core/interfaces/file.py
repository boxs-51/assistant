from abc import ABC, abstractmethod
from typing import Any

class FileProvider(ABC):
    # =========================
    # Files
    # =========================
    @abstractmethod
    async def upload_file(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def download_file(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def delete_file(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def list_files(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def get_file(self, **kwargs) -> Any: raise NotImplementedError