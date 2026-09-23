from __future__ import annotations

import asyncio
import math
from time import monotonic
from abc import ABC
from typing import Any, Dict, Optional

import structlog

from ...circuit_breaker import CircuitBreakerManager
from ..exceptions import ProviderDeadlineExceededError
from ..executor import ProviderExecutor
from ..policies.routing_policy import RoutingPolicy
from ..retry_contracts import ProviderCallBudget

logger = structlog.get_logger(__name__)


class BaseExecutionHandler(ABC):
    """Base class for provider execution handlers."""

    def __init__(
        self,
        providers: Dict[str, Any],
        routing_policy: Optional[RoutingPolicy],
        executor: ProviderExecutor,
        circuit_breaker_manager: CircuitBreakerManager,
        timeout: float | None = None,
    ):
        self.providers = providers
        self.routing_policy = routing_policy
        self.executor = executor
        self.circuit_breaker_manager = circuit_breaker_manager
        self.timeout = 60.0 if timeout is None else float(timeout)

    def _new_call_budget(
        self,
        caller_deadline_monotonic: float | None = None,
    ) -> ProviderCallBudget:
        """Create one logical budget bounded by caller and provider deadlines."""

        configured_timeout = float(self.timeout)
        if (
            not math.isfinite(configured_timeout)
            or configured_timeout <= 0
        ):
            raise ProviderDeadlineExceededError(
                "Provider call deadline is already exhausted."
            )

        now = monotonic()
        effective_deadline = now + configured_timeout

        if caller_deadline_monotonic is not None:
            if isinstance(caller_deadline_monotonic, bool):
                raise ProviderDeadlineExceededError(
                    "Caller provider deadline is invalid or exhausted."
                )
            try:
                caller_deadline = float(caller_deadline_monotonic)
            except (TypeError, ValueError) as exc:
                raise ProviderDeadlineExceededError(
                    "Caller provider deadline is invalid or exhausted."
                ) from exc
            if not math.isfinite(caller_deadline):
                raise ProviderDeadlineExceededError(
                    "Caller provider deadline is invalid or exhausted."
                )
            effective_deadline = min(
                effective_deadline,
                caller_deadline,
            )

        if effective_deadline <= now:
            raise ProviderDeadlineExceededError(
                "Provider call deadline is already exhausted."
            )

        retry_policy = getattr(self.executor, "retry_policy", None)
        max_retries = getattr(retry_policy, "max_retries", 0)
        if (
            not isinstance(max_retries, int)
            or isinstance(max_retries, bool)
            or max_retries < 0
        ):
            max_retries = 0

        return ProviderCallBudget(
            deadline_monotonic=effective_deadline,
            max_retries=max_retries,
        )

    @staticmethod
    def _remaining_timeout(
        call_budget: ProviderCallBudget,
        *,
        provider_name: str | None = None,
    ) -> float:
        remaining = call_budget.remaining_seconds(
            now_monotonic=monotonic()
        )
        if remaining <= 0:
            raise ProviderDeadlineExceededError(
                "Provider call deadline exceeded.",
                provider_name=provider_name,
            )
        return remaining

    async def _get_healthy_fallback_chain(self, initial_chain: list) -> list:
        health_checks = [
            self.executor.is_provider_healthy(provider.name)
            for provider in initial_chain
        ]
        health_results = await asyncio.gather(*health_checks)

        healthy_chain = []
        for index, provider in enumerate(initial_chain):
            if health_results[index]:
                healthy_chain.append(provider)
            else:
                logger.warning(
                    "Excluding unhealthy provider",
                    provider=provider.name,
                )
        return healthy_chain
