from abc import ABC, abstractmethod
from typing import Dict, Any, AsyncGenerator
import httpx

from ...schemas import GatewayResponse, GatewayStreamChunk

class BaseProvider(ABC):
    """
    Interface Provider (Adapter): Chịu trách nhiệm gửi request,
    và quan trọng nhất là **chuẩn hóa** response về định dạng chung của Gateway.
    """
    def __init__(self, name: str, api_url: str, headers: Dict[str, str]):
        self.name = name
        self.api_url = api_url
        self.headers = headers

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem provider này đã được cấu hình đầy đủ trong settings hay chưa."""
        pass

    @abstractmethod
    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        """Thực hiện một request POST đến provider."""
        pass

    @abstractmethod
    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        """Chuẩn hóa response (non-streaming) của provider về GatewayResponse."""
        pass

    @abstractmethod
    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuẩn hóa response (streaming) của provider về một generator các GatewayStreamChunk."""
        pass