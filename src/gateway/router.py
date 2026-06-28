import httpx
from typing import List, Dict, Any, Tuple, Coroutine
import asyncio

import structlog
from opentelemetry import trace

from .config import settings
from .routing.exceptions import NoAvailableProviderError, ProviderError
from .routing.executor import ProviderExecutor
from .routing.policies.routing_policy import RoutingPolicy
from .routing.policies.circuit_breaker import CircuitBreakerManager
from .schemas import GatewayResponse, GatewayStreamChunk # Import schema
from .routing.providers.base import BaseProvider

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
    def __init__(self, providers: Dict[str, BaseProvider], routing_policy: RoutingPolicy, circuit_breaker_manager: CircuitBreakerManager):
        # Dependency Injection: Nhận tất cả các thành phần phụ thuộc
        self.executor = ProviderExecutor(circuit_breaker_manager)
        self.providers = providers
        # Policy giờ đây cần danh sách provider để tự khởi tạo
        self.routing_policy = routing_policy

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

    async def execute_with_fallback(self, http_client: httpx.AsyncClient, model: str, body: Dict[str, Any]) -> GatewayResponse:
        # 1. Lấy chuỗi fallback một cách linh động từ policy cho mỗi request
        initial_chain = self.routing_policy.get_fallback_chain(model)
        if not initial_chain:
            raise NoAvailableProviderError(f"No provider configured for model '{model}' or default.")

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
            logger.critical("All providers in the execution chain are unhealthy (circuit open).", model=model, initial_chain=[p.name for p in execution_chain])
            raise NoAvailableProviderError("All configured providers are currently unavailable (circuit breakers are open).")

        last_exception = None
        # 4. Thực thi tuần tự theo chuỗi đã được xác định
        for provider in healthy_execution_chain:
            with trace.get_tracer(__name__).start_as_current_span(f"provider_attempt:{provider.name}") as span:
                span.set_attribute("provider.name", provider.name); span.set_attribute("model.name", model)
                try:
                    logger.info("Attempting to call provider", provider=provider.name, model=model)
                    # Executor giờ trả về GatewayResponse đã được chuẩn hóa
                    gateway_response = await self.executor.execute(provider, http_client, body)
                    logger.info("Provider call successful", provider=provider.name)
                    span.set_attribute("provider.success", True)
                    return gateway_response
                except (ProviderError, httpx.RequestError, httpx.HTTPStatusError) as e:
                    span.record_exception(e)
                    logger.warning("Provider execution failed, attempting next in fallback chain", provider=provider.name, error=str(e))
                    last_exception = e
                    continue

        logger.critical("All providers in fallback chain failed", model=model)
        raise NoAvailableProviderError("All providers are currently unavailable.") from last_exception