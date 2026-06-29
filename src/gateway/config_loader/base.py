from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseConfigSource(ABC):
    """Interface cho một nguồn cấu hình."""

    @abstractmethod
    def load(self) -> Dict[str, Any]:
        """
        Tải cấu hình từ nguồn và trả về dưới dạng một dictionary.
        Trả về một dict rỗng nếu nguồn không có sẵn hoặc không có dữ liệu.
        """
        pass