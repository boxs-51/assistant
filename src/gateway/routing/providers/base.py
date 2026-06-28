from abc import ABC, abstractmethod
from typing import Dict, Any
import httpx

class BaseProvider(ABC):
    """
    Interface Provider: Chỉ chịu trách nhiệm gửi request HTTP.
    Hoàn toàn không chứa logic về retry, timeout, circuit breaker.
    """
    def __init__(self, name: str, api_url: str, headers: Dict[str, str]):
        self.name = name
        self.api_url = api_url
        self.headers = headers

    @abstractmethod
    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        """Thực hiện một request POST đến provider."""
        pass