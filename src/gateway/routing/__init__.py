import httpx
from typing import List, Dict, Any, Tuple, Coroutine, AsyncGenerator
import asyncio

import structlog
from opentelemetry import trace

from ..config import settings
from .exceptions import NoAvailableProviderError, ProviderError
from .executor import ProviderExecutor
from .policies.routing_policy import RoutingPolicy 
from .registry import ProviderRegistry
from .discovery import ProviderDiscovery
from ...circuit_breaker.circuit_breaker import CircuitBreakerManager
from ..schemas import GatewayResponse, GatewayStreamChunk, ModelCapability
from .providers.base.provider import BaseProvider

import asyncio, anyio
logger = structlog.get_logger(__name__)

class ModelRouter:
    """
    REFACTORED: Lớp điều phối chính, chỉ chịu trách nhiệm điều phối luồng request.
    Nó ủy quyền việc xây dựng bảng định tuyến và thực thi cho các thành phần chuyên biệt.
    """
    # Định nghĩa nhóm provider để hỗ trợ định tuyến local/cloud
    PROVIDER_TYPES = {
        "ollama": "local",
        "openai": "cloud",
        "gemini": "cloud",
        "anthropic": "cloud",
    }
    def __init__(self, circuit_breaker_manager: CircuitBreakerManager):
        # Dependency Injection: Nhận tất cả các thành phần phụ thuộc
        provider_registry = ProviderRegistry()
        provider_discovery = ProviderDiscovery(registry=provider_registry)
        provider_discovery.run() # Chạy quá trình khám phá

        available_providers = provider_registry.list_all_providers()
        routing_policy = RoutingPolicy(providers=available_providers)
        self.executor = ProviderExecutor(circuit_breaker_manager)
        
        self.providers = available_providers
        # Policy giờ đây cần danh sách provider để tự khởi tạo
        self.routing_policy = routing_policy

        logger.info(
            "Providers",
            providers=list(available_providers.keys())
        )
        if not self.providers:
            raise RuntimeError("Configuration Error: No LLM providers are enabled.")

    async def _get_healthy_fallback_chain(self, initial_chain: List[BaseProvider]) -> List[BaseProvider]:
        """
        Lọc chuỗi fallback, chỉ giữ lại các provider có circuit breaker không ở trạng thái OPEN.
        """
        health_checks: List[Coroutine] = [
            self.executor.is_provider_healthy(provider.name) for provider in initial_chain
        ]
        health_results: List[bool] = await asyncio.gather(*health_checks)

        healthy_chain: List[BaseProvider] = []
        for i, provider in enumerate(initial_chain):
            if health_results[i]:
                healthy_chain.append(provider)
            else:
                logger.warning("Excluding unhealthy provider from fallback chain", provider=provider.name, reason="Circuit breaker is OPEN")

        return healthy_chain

    async def execute_with_fallback(self, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> GatewayResponse:
        # 1. Lấy chuỗi fallback một cách linh động từ policy cho mỗi request
        initial_chain = self.routing_policy.get_fallback_chain(body.get("model"))
        if not initial_chain:
            raise NoAvailableProviderError(f"No provider configured for model '{body.get('model')}' or default.")

        # 2. Logic định tuyến ưu tiên theo yêu cầu của client
        execution_chain = initial_chain
        specific_provider_name = body.get("provider")
        provider_preference = body.get("provider_preference")

        # Ưu tiên cao nhất: Client yêu cầu một provider cụ thể
        if specific_provider_name and specific_provider_name in self.providers:
            preferred_provider = self.providers[specific_provider_name]
            # Các provider còn lại trong chuỗi, loại bỏ provider đã được ưu tiên
            others = [p for p in initial_chain if p.name != specific_provider_name]
            execution_chain = [preferred_provider] + others
            logger.info(f"Prioritizing provider '{specific_provider_name}' based on request.", chain=[p.name for p in execution_chain])
        # Ưu tiên thứ hai: Client yêu cầu một nhóm provider (local/cloud)
        elif provider_preference in ["local", "cloud"]:
            # Phân tách danh sách thành 2 nhóm: ưu tiên và còn lại
            preferred = [p for p in initial_chain if self.PROVIDER_TYPES.get(p.name) == provider_preference]
            others = [p for p in initial_chain if self.PROVIDER_TYPES.get(p.name) != provider_preference]
            # Tạo chuỗi thực thi mới với nhóm ưu tiên được đặt lên đầu
            execution_chain = preferred + others
            logger.info(f"Re-ordered execution chain based on preference '{provider_preference}'", chain=[p.name for p in execution_chain])

        # 3. [MỚI] Lọc các provider không khỏe mạnh (circuit open) khỏi chuỗi thực thi
        healthy_execution_chain = await self._get_healthy_fallback_chain(execution_chain)

        if not healthy_execution_chain:
            logger.critical("All providers in the execution chain are unhealthy (circuit open).", model=body.get("model"), initial_chain=[p.name for p in execution_chain])
            raise NoAvailableProviderError("All configured providers are currently unavailable (circuit breakers are open).")

        last_exception = None
        # 4. Thực thi tuần tự theo chuỗi đã được xác định
        for provider in healthy_execution_chain:
            with trace.get_tracer(__name__).start_as_current_span(f"provider_attempt:{provider.name}") as span:
                span.set_attribute("provider.name", provider.name); span.set_attribute("model.name", body.get("model"))
                try:
                    # [CẢI TIẾN] Kiểm tra năng lực ngay trước khi thực thi (lazy check)
                    if not await provider.has_capability(body.get("model"), ModelCapability.CHAT, http_client, settings.provider.timeout):
                        logger.warning("Skipping provider as it does not support CHAT capability for the model.", provider=provider.name, model=body.get("model"))
                        continue

                    logger.info("Attempting to call provider", provider=provider.name, model=body.get("model"))
                    # Executor giờ trả về GatewayResponse đã được chuẩn hóa
                    gateway_response = await self.executor.execute(provider=provider, http_client=http_client, body=body)
                    logger.info("Provider call successful", provider=provider.name)
                    span.set_attribute("provider.success", True)
                    return gateway_response
                except (ProviderError, httpx.RequestError, httpx.HTTPStatusError) as e:
                    span.record_exception(e)
                    logger.warning("Provider execution failed, attempting next in fallback chain", provider=provider.name, error=str(e))
                    last_exception = e
                    continue

        logger.critical("All providers in fallback chain failed", model=body.get("model"))
        raise NoAvailableProviderError("All providers are currently unavailable.") from last_exception

    async def stream_with_fallback(self, http_client: httpx.AsyncClient, body: Dict[str, Any]) -> AsyncGenerator[GatewayStreamChunk, None]:
        # 1. & 2. Lấy và sắp xếp chuỗi fallback (logic giống hệt execute_with_fallback)
        initial_chain = self.routing_policy.get_fallback_chain(body.get("model"))
        if not initial_chain:
            raise NoAvailableProviderError(f"No provider configured for model '{body.get('model')}' or default.")

        execution_chain = initial_chain
        specific_provider_name = body.get("provider")
        provider_preference = body.get("provider_preference")

        if specific_provider_name and specific_provider_name in self.providers:
            preferred_provider = self.providers[specific_provider_name]
            others = [p for p in initial_chain if p.name != specific_provider_name]
            execution_chain = [preferred_provider] + others
        elif provider_preference in ["local", "cloud"]:
            preferred = [p for p in initial_chain if self.PROVIDER_TYPES.get(p.name) == provider_preference]
            others = [p for p in initial_chain if self.PROVIDER_TYPES.get(p.name) != provider_preference]
            execution_chain = preferred + others
        
        # 3. [MỚI] Lọc các provider không hỗ trợ streaming
        
        # Tạo coroutine để kiểm tra năng lực streaming cho từng provider
        stream_check_coroutines = [
            p.has_capability(body.get("model"), ModelCapability.CHAT_STREAM, http_client, settings.provider.timeout)
            for p in execution_chain
        ]
        # Chạy kiểm tra song song
        stream_check_results = await asyncio.gather(*stream_check_coroutines, return_exceptions=True)

        stream_capable_chain = [p for i, p in enumerate(execution_chain) if stream_check_results[i] is True]

        if not stream_capable_chain:
            raise NoAvailableProviderError(f"No providers configured for model '{body.get('model')}' support streaming.")

        # 4. Lọc các provider không khỏe mạnh
        healthy_execution_chain = await self._get_healthy_fallback_chain(stream_capable_chain)
        if not healthy_execution_chain:
            logger.critical("All providers in the execution chain are unhealthy (circuit open).", model=body.get("model"))
            raise NoAvailableProviderError("All configured providers are currently unavailable (circuit breakers are open).")

        # 5. Thực thi streaming tuần tự
        for provider in healthy_execution_chain:
            try:
                logger.info("Attempting to stream from provider", provider=provider.name, model=body.get("model"))
                # Sử dụng executor.execute_stream và yield from
                async for chunk in self.executor.execute_stream(provider=provider, http_client=http_client, body=body):
                    yield chunk
                return # Nếu stream thành công, kết thúc generator
            except (ProviderError, httpx.RequestError, httpx.HTTPStatusError) as e:
                logger.warning("Provider stream failed, attempting next in fallback chain", provider=provider.name, error=str(e))
                last_exception = e
                continue

        logger.critical("All providers in fallback chain failed for streaming", model=body.get("model"))
        raise NoAvailableProviderError("All providers are currently unavailable for streaming.") from last_exception
        
    async def execute_embeddings(self, http_client: httpx.AsyncClient, body: dict) -> dict:
        """Thực thi request embeddings với fallback và kiểm tra năng lực."""
        provider_list = self.routing_policy.get_provider_list(body)
        
        # Lọc các provider hỗ trợ embeddings cho model
        capable_providers_coroutines = [
            (p, p.has_capability(body.get("model"), ModelCapability.EMBEDDINGS, http_client, settings.provider.timeout))
            for p_name in provider_list if (p := self.providers.get(p_name))
        ]
        capable_results = await asyncio.gather(*[coro for _, coro in capable_providers_coroutines])
        
        execution_chain = [
            provider for i, (provider, _) in enumerate(capable_providers_coroutines) if capable_results[i] is True
        ]

        if not execution_chain:
            raise NoAvailableProviderError(f"No providers support embeddings for model '{body.get('model')}'.")

        last_error = None
        for provider in execution_chain:
            try:
                # Định nghĩa hàm gọi cụ thể cho embeddings
                execution_callable = lambda: provider.embeddings(http_client=http_client, body=body, timeout=settings.provider.timeout)
                # Dùng executor mới
                result = await self.executor.execute_generic(provider, execution_callable)
                return result
            except ProviderError as e:
                last_error = e
                continue

        raise NoAvailableProviderError("All providers failed for embeddings request.") from last_error

    async def list_models(self, http_client: httpx.AsyncClient) -> List[dict]:
        """Lấy danh sách model từ tất cả các provider có sẵn."""
        async with anyio.create_task_group() as tg:
            for provider in self.providers.values():
                # Bỏ qua các provider chưa triển khai `models`
                if "NotImplementedError" in str(provider.models): continue
                tg.start_soon(provider.models, http_client, settings.provider.timeout)
        # Đây là một cách đơn giản, trong thực tế cần xử lý lỗi cho từng provider
        return [{"provider": "example", "models": []}] # Placeholder
