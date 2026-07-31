from abc import ABC, abstractmethod
from typing import Optional

from .....domain.schemas.identity import Identity

class AuthenticatorInterface(ABC):
    """
    Interface (hợp đồng) cho tất cả các chiến lược xác thực.
    Mỗi authenticator phải có khả năng kiểm tra xem nó có thể xử lý token hay không,
    và thực hiện việc xác thực nếu có thể.
    """
    @abstractmethod
    def can_handle(self, token: str) -> bool:
        """Kiểm tra xem authenticator này có thể xử lý định dạng token được cung cấp hay không."""
        raise NotImplementedError

    @abstractmethod
    async def authenticate(self, token: str) -> Optional[Identity]:
        """Thực hiện logic xác thực và trả về một đối tượng Identity nếu thành công."""
        raise NotImplementedError