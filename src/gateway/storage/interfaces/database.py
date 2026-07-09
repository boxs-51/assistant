from abc import ABC, abstractmethod
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

class DatabaseDriver(ABC):
    """
    Interface trừu tượng cho tất cả các driver cơ sở dữ liệu quan hệ (SQL).
    """

    @abstractmethod
    async def connect(self):
        """Khởi tạo engine và kiểm tra kết nối."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Đóng engine và giải phóng tài nguyên."""
        pass

    @abstractmethod
    def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Cung cấp một session bất đồng bộ để tương tác với CSDL."""
        pass