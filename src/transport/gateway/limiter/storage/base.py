from abc import ABC, abstractmethod
from typing import Any, Tuple


class BaseStorage(ABC):
    """Interface cho các backend lưu trữ của Rate Limiter."""

    @abstractmethod
    async def execute(
        self, script_name: str, keys: list, args: list
    ) -> Any:
        """Thực thi một script đã được đăng ký với các key và argument đã cho."""
        pass