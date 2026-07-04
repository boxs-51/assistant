from abc import ABC, abstractmethod
from typing import Dict, Any, AsyncGenerator

import httpx
from ....schemas import GatewayResponse, GatewayStreamChunk

class BaseAdapter(ABC):
    """
    Trừu tượng hóa việc chuyển đổi request/response giữa định dạng
    của Gateway và định dạng của provider cụ thể.
    """
    @abstractmethod
    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Chuyển đổi request body từ chuẩn Gateway sang chuẩn provider."""
        pass

    @abstractmethod
    async def adapt_chat_response(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ provider về GatewayResponse."""
        pass

    @abstractmethod
    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi stream từ provider về stream các GatewayStreamChunk."""
        pass

    def adapt_embeddings_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Chuyển đổi request body cho embeddings. Mặc định là không thay đổi."""
        return request

    async def adapt_embeddings_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Chuyển đổi response cho embeddings. Mặc định là trả về JSON gốc."""
        return await response.json()