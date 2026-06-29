from abc import ABC, abstractmethod
from typing import Dict, Any, AsyncGenerator

from ....schemas import GatewayResponse, GatewayStreamChunk

class BaseAdapter(ABC):
    """
    Trừu tượng hóa việc chuyển đổi request/response giữa định dạng
    của Gateway và định dạng của provider cụ thể.
    """
    @abstractmethod
    def adapt_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Chuyển đổi request body từ chuẩn Gateway sang chuẩn provider."""
        pass

    @abstractmethod
    def adapt_response(self, response_data: Dict[str, Any], model: str) -> GatewayResponse:
        """Chuyển đổi response JSON từ provider về GatewayResponse."""
        pass

    @abstractmethod
    async def adapt_stream(self, response_iterator: AsyncGenerator[bytes, None], model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi stream từ provider về stream các GatewayStreamChunk."""
        pass