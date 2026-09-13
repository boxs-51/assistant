from abc import ABC, abstractmethod
from typing import List, Optional

from ..core.provider import BaseProvider


class BaseLoadBalancer(ABC):
    """
    Interface (Strategy) cho tất cả các thuật toán cân bằng tải.
    Nó nhận vào một danh sách các provider và cung cấp một phương thức để chọn ra
    provider tiếp theo cần thử.
    """

    def __init__(self, providers: List[BaseProvider]):
        self.providers = providers

    @abstractmethod
    def select_provider(self) -> Optional[BaseProvider]:
        """
        Chọn provider tiếp theo từ danh sách.

        Returns:
            Một instance của BaseProvider, hoặc None nếu không còn provider nào để thử.
        """
        pass


class RoundRobinLoadBalancer(BaseLoadBalancer):
    """
    Triển khai chiến lược cân bằng tải Round Robin (xoay vòng).
    Nó sẽ lặp qua danh sách các provider một cách tuần tự.
    """
    def __init__(self, providers: List[BaseProvider]):
        super().__init__(providers)
        self._current_index = 0

    def select_provider(self) -> Optional[BaseProvider]:
        if not self.providers or self._current_index >= len(self.providers):
            return None
        
        provider = self.providers[self._current_index]
        self._current_index += 1
        return provider