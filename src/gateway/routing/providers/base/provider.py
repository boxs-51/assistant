from .adapter import BaseAdapter
from .auth import AuthStrategy
from .endpoint import EndpointBuilder
from .capability import ProviderCapability
from .model_mapper import ModelMapper
from abc import ABC, abstractmethod
import httpx
from typing import Dict, Any, AsyncGenerator

from ....schemas import GatewayResponse, GatewayStreamChunk

class BaseProvider(ABC):
    """
    Một container cho các thành phần cấu thành nên một provider.
    Sử dụng Composition over Inheritance.
    """
    def __init__(
        self,
        provider_name: str,
        auth_strategy: AuthStrategy,
        endpoint_builder: EndpointBuilder,
        adapter: BaseAdapter,
        model_mapper: ModelMapper,
        capabilities: set[ProviderCapability]
    ):
        self.name = provider_name
        self.auth = auth_strategy
        self.endpoints = endpoint_builder
        self.adapter = adapter
        self.mapper = model_mapper
        self.capabilities = capabilities

    def has_capability(self, capability: ProviderCapability) -> bool:
        return capability in self.capabilities

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def request(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> httpx.Response:
        """
        Thực hiện request HTTP thô đến API của provider.
        Lớp con phải triển khai logic để xây dựng URL, body, và headers.
        """
        raise NotImplementedError

    @abstractmethod
    async def normalize_response(self, response: httpx.Response, model: str) -> GatewayResponse:
        """Chuyển đổi một httpx.Response thành một GatewayResponse đã được chuẩn hóa."""
        raise NotImplementedError

    @abstractmethod
    async def normalize_stream(self, response: httpx.Response, model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi một httpx.Response streaming thành một generator các GatewayStreamChunk."""
        raise NotImplementedError