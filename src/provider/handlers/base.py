from abc import ABC
from typing import Dict, Any, Optional
import httpx
from ...circuit_breaker import CircuitBreakerManager
from ..policies.routing_policy import RoutingPolicy
from ..executor import ProviderExecutor

class BaseExecutionHandler(ABC):
    """Lớp cơ sở cho mọi Handler thực thi tác vụ Provider."""

    def __init__(
        self,
        providers: Dict[str, Any],
        routing_policy: Optional[RoutingPolicy],
        executor: ProviderExecutor,
        circuit_breaker_manager: CircuitBreakerManager
    ):
        self.providers = providers
        self.routing_policy = routing_policy
        self.executor = executor
        self.circuit_breaker_manager = circuit_breaker_manager

    async def _get_healthy_fallback_chain(self, initial_chain: list) -> list:
        import asyncio
        import structlog
        logger = structlog.get_logger(__name__)

        health_checks = [self.executor.is_provider_healthy(p.name) for p in initial_chain]
        health_results = await asyncio.gather(*health_checks)

        healthy_chain = []
        for i, provider in enumerate(initial_chain):
            if health_results[i]:
                healthy_chain.append(provider)
            else:
                logger.warning("Excluding unhealthy provider", provider=provider.name)
        return healthy_chain