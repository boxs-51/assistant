
from .auth import AuthStrategy
from .api import ApiType
from .endpoint import EndpointBuilder
from ....schemas import ProviderCapability, ModelCapability
from .api_mapper import ApiTypeMapper
from .capability import ModelCapabilityManager
from .model_mapper import ModelMapper
from abc import ABC, abstractmethod
import httpx
import structlog
from typing import Dict, Any, Set, Union

logger = structlog.get_logger(__name__)

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
        api_mapper: ApiTypeMapper,
        model_mapper: ModelMapper,
        capability_manager: ModelCapabilityManager,
        provider_capabilities: Set[ProviderCapability] = set()
    ):
        super().__init__()
        self.name = provider_name
        self.auth = auth_strategy
        self.endpoints = endpoint_builder
        self.api_mapper = api_mapper
        self.mapper = model_mapper
        self.capability_manager = capability_manager
        self.provider_capabilities = provider_capabilities

    def has_provider_capability(self, capability: ProviderCapability) -> bool:
        """Kiểm tra xem nhà cung cấp có hỗ trợ một năng lực nhất định hay không."""
        return capability in self.provider_capabilities

    async def has_capability(self, model_name: str, capability: ModelCapability, http_client: httpx.AsyncClient, timeout: float) -> bool:
        """Kiểm tra xem một model cụ thể có hỗ trợ một năng lực nhất định hay không."""
        # Ủy quyền hoàn toàn cho capability_manager
        model_caps = await self.capability_manager.get_capabilities_for_model(
            provider=self,
            model_name=model_name,
            http_client=http_client,
            timeout=timeout
        )
        return capability in model_caps


    def build_endpoint(
        self,
        api_type: Union[ApiType, str],
        **kwargs
    ) -> str:
        """
        Xây dựng URL endpoint hoàn chỉnh.
        - Nếu `api_type` là ApiType (Enum): Tự động lấy template từ mapper hệ thống.
        - Nếu `api_type` là str: Sử dụng chuỗi đó trực tiếp làm template, bỏ qua mapper.
        """
        # 1. Xác định template dựa trên kiểu dữ liệu của api_type
        if isinstance(api_type, str):
            template = api_type
            logger.debug(
                "Building endpoint using direct string template",
                provider=self.name,
                template=template,
                variables=kwargs
            )
        else:
            template = self.api_mapper.get_template(api_type)
            logger.debug(
                "Building endpoint using mapped ApiType template",
                provider=self.name,
                api_type=str(api_type),
                template=template,
                variables=kwargs
            )

        # 2. Thực hiện compile/build URL từ template và các tham số truyền vào
        try:
            final_endpoint = self.endpoints.build(
                template,
                **kwargs
            )
            return final_endpoint
        except Exception as e:
            logger.error(
                "Failed to parse or format endpoint template",
                provider=self.name,
                template=template,
                variables=kwargs,
                error=str(e)
            )
            raise e

    async def send(
        self,
        client: httpx.AsyncClient,
        api_type: Union[ApiType, str],
        *,
        method: str = "POST",
        json: Dict[str, Any] | None = None,
        data: Dict[str, Any] | None = None,
        params: Dict[str, Any] | None = None,
        files: Any = None,
        headers: Dict[str, str] | None = None,
        timeout: float,
        **endpoint_kwargs,
    ) -> httpx.Response:
        """
        Gom logic gửi request HTTP vào một nơi, hỗ trợ nhiều phương thức và loại body.
        Đã được bổ sung logger chi tiết để phục vụ truy vết lỗi.
        """
        if headers is None:
            headers = {"Content-Type": "application/json"}
            
        # 1. Log quá trình build URL và xử lý Auth
        try:
            request_url = self.build_endpoint(api_type, **endpoint_kwargs)
            auth_url, auth_headers = self.auth.prepare_request(request_url, headers)
            
            # Tạo bản sao headers không lộ API key hoàn toàn nếu log ra (Tùy chọn bảo mật)
            masked_headers = {k: (v if "key" not in k.lower() and "auth" not in k.lower() else "***") for k, v in auth_headers.items()}
            
            logger.info(
                "Preparing Outgoing HTTP Request",
                provider=self.name,
                api_type=str(api_type),
                method=method,
                endpoint_kwargs=endpoint_kwargs,
                headers=masked_headers
            )
            # Nếu muốn soi kỹ JSON gửi đi khi debug, bật dòng này:
            if json:
                logger.debug("Outgoing Request Body JSON", provider=self.name, json_body=json)
                
        except Exception as e:
            logger.error(
                "Failed to build endpoint or prepare auth strategy",
                provider=self.name,
                api_type=str(api_type),
                error=str(e)
            )
            raise e

        # 2. Thực hiện gửi request và đo đạc phản hồi
        try:
            response = await client.request(
                method=method,
                url=auth_url,
                json=json,
                data=data,
                files=files,
                params=params,
                headers=auth_headers,
                timeout=timeout,
            )
            
            logger.info(
                "Received HTTP Response",
                provider=self.name,
                status_code=response.status_code,
                elapsed_seconds=response.elapsed.total_seconds(),
                url=str(response.url) # Log URL thực tế (có thể kèm query params)
            )
            
            # Kích hoạt raise_for_status để bắt các lỗi 4xx, 5xx
            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as exc:
            # Bắt lỗi HTTP từ phía Provider (Ví dụ: 400 Bad Request, 403 Forbidden, 503 Service Unavailable)
            # Cố gắng đọc nội dung lỗi để chỉ ra nguyên nhân chính xác
            try:
                error_body = exc.response.json()
            except Exception:
                error_body = exc.response.text

            logger.error(
                "HTTP Status Error from Provider",
                provider=self.name,
                status_code=exc.response.status_code,
                url=str(exc.request.url),
                error_response=error_body
            )
            raise exc
            
        except httpx.RequestError as exc:
            # Bắt lỗi kết nối vật lý (Ví dụ: Timeout, DNS thất bại, rớt mạng)
            logger.error(
                "HTTP Request Network/Connection Error",
                provider=self.name,
                url=str(exc.request.url),
                error_type=type(exc).__name__,
                message=str(exc)
            )
            raise exc

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Kiểm tra xem provider đã được cấu hình đúng cách hay chưa."""
        raise NotImplementedError


    @abstractmethod
    async def moderation(self, **kwargs) -> Any: raise NotImplementedError
    # =========================
    # Computer Use
    # =========================
    @abstractmethod
    async def computer_use(self, **kwargs) -> Any: raise NotImplementedError

    # =========================
    # Metadata
    # =========================
    @abstractmethod
    async def provider_info(self, **kwargs) -> Any: raise NotImplementedError
    @abstractmethod
    async def health(self, **kwargs) -> Any: raise NotImplementedError