from .adapter import BaseAdapter
from .auth import AuthStrategy
from .api import ApiType
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

    def prepare_body(self, body: Dict[str, Any], default_model: str | None = None) -> Dict[str, Any]:
        """
        Chuẩn bị body cho request: dịch tên model và adapt body.
        Loại bỏ logic lặp lại ở các provider con.
        """
        prepared_body = body.copy()
        # Dịch tên model, sử dụng default_model nếu có, hoặc lấy từ body, hoặc 'default'
        model_to_translate = body.get("model") or default_model or "default"
        translated_model = self.mapper.translate(model_to_translate)
        prepared_body["model"] = translated_model
        
        return self.adapter.adapt_chat_request(prepared_body)

    async def send(
        self,
        client: httpx.AsyncClient,
        api_type: ApiType | str,
        body: Dict[str, Any],
        timeout: float,
        headers: Dict[str, str] | None = None,
        **endpoint_kwargs
    ) -> httpx.Response:
        """
        Gom logic gửi request HTTP POST vào một nơi.
        """
        if headers is None:
            headers = {"Content-Type": "application/json"}
            
        request_url = self.endpoints.build(api_type, **endpoint_kwargs)
        final_url, auth_headers = self.auth.prepare_request(request_url, headers)
        
        response = await client.post(final_url, json=body, headers=auth_headers, timeout=timeout)
        response.raise_for_status()
        return response

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def chat(self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float) -> GatewayResponse:
        """
        Thực hiện một yêu cầu chat hoàn chỉnh (non-streaming).
        Phương thức này bao gồm việc gửi request và chuẩn hóa response.
        """
        raise NotImplementedError

    @abstractmethod
    async def chat_stream(
        self, http_client: httpx.AsyncClient, body: Dict[str, Any], timeout: float
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """
        Thực hiện một yêu cầu chat streaming.
        Phương thức này trả về một async generator đã được chuẩn hóa.
        """
        raise NotImplementedError