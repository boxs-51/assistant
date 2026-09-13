from typing import Any, Dict

import structlog

from ..application.container import ApplicationContainer

logger = structlog.get_logger(__name__)


class AdminService:
    """Application service for provider/runtime administration."""

    def __init__(self, container: ApplicationContainer):
        provider_runtime = container.provider_runtime
        if provider_runtime is None:
            raise RuntimeError("ProviderRuntime is not initialized.")

        self.routing_policy = provider_runtime.routing_policy
        self.circuit_breaker_manager = container.circuit_breaker_manager

    async def reload_routing_rules(self) -> bool:
        if self.routing_policy is None:
            logger.error("Routing policy is not initialized")
            return False

        try:
            return await self.routing_policy.reload_rules()
        except Exception as exc:
            logger.exception("Error during routing rules hot-reload", error=str(exc))
            return False

    async def get_circuit_breaker_statuses(self) -> Dict[str, Any]:
        if self.circuit_breaker_manager is None:
            logger.error("Circuit Breaker Manager is not initialized")
            return {}

        return await self.circuit_breaker_manager.get_all_statuses()
