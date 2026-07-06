from abc import ABC
from typing import Dict, Any, AsyncGenerator, List

import httpx
from ....schemas import GatewayResponse, GatewayStreamChunk, GatewayAttachment

class BaseAdapter(ABC):
    """
    Trừu tượng hóa việc chuyển đổi request/response giữa định dạng
    của Gateway và định dạng của provider cụ thể.
    """
    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Chuyển đổi request body từ chuẩn Gateway sang chuẩn provider."""
        return request

    async def adapt_chat_response(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ provider về GatewayResponse."""
        return await response.json()


    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi stream từ provider về stream các GatewayStreamChunk."""
        return response.aiter_text()


    def adapt_embeddings_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Chuyển đổi request body cho embeddings. Mặc định là không thay đổi."""
        return request

    async def adapt_embeddings_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Chuyển đổi response cho embeddings. Mặc định là trả về JSON gốc."""
        return await response.json()
    
    async def adapt_file_upload_response(self, response: Any) -> GatewayAttachment:
        """Chuyển đổi response từ provider về GatewayAttachment."""
        return response
    
    async def adapt_file_list_response(self, response: Any) -> List[GatewayAttachment]:
        return response
