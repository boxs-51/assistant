from abc import ABC, abstractmethod
from typing import Tuple

from ..storage.base import BaseStorage


class BaseRateLimiter(ABC):
    """Interface cho tất cả các thuật toán Rate Limiter."""

    def __init__(self, storage: BaseStorage, **kwargs):
        self.storage = storage

    @abstractmethod
    async def is_allowed(self, key: str, cost: int = 1) -> Tuple[bool, int, float]:
        """
        Kiểm tra xem một request có được phép hay không.

        Returns:
            Một tuple chứa: (được phép, số request còn lại, thời gian chờ).
        """
        pass