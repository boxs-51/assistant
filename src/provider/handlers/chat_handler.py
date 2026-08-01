import asyncio
import httpx
import structlog
from typing import Dict, Any, AsyncGenerator
from opentelemetry import trace

from .base import BaseExecutionHandler
from ..exceptions import NoAvailableProviderError, ProviderError
from ...domain.schemas import GatewayResponse, GatewayStreamChunk, ModelCapability
from ...infrastructure.config import settings

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

class ChatExecutionHandler(BaseExecutionHandler):
    """Xử lý thực thi Chat Sync và Streaming Chat với chế độ Fallback."""

    async def execute_with_fallback(
        self, http_client: httpx.AsyncClient, body: Dict[str, Any]
    ) -> GatewayResponse:
        model = body.get("model")
        initial_chain = self.routing_policy.get_fallback_chain(model)
        if not initial_chain:
            raise NoAvailableProviderError(f"No provider configured for model '{model}'.")

        execution_chain = initial_chain
        specific_provider_name = body.get("provider")

        if specific_provider_name and specific_provider_name in self.providers:
            preferred_provider = self.providers[specific_provider_name]
            others = [p for p in initial_chain if p.name != specific_provider_name]
            execution_chain = [preferred_provider] + others

        healthy_execution_chain = await self._get_healthy_fallback_chain(execution_chain)
        if not healthy_execution_chain:
            raise NoAvailableProviderError("All providers are currently unavailable (circuit breakers open).")

        last_exception = None
        for provider in healthy_execution_chain:
            with tracer.start_as_current_span(f"provider_attempt:{provider.name}") as span:
                span.set_attribute("provider.name", provider.name)
                try:
                    if not await provider.has_capability(
                        body.get("model"), ModelCapability.CHAT, http_client, settings.provider.timeout
                    ):
                        continue

                    return await self.executor.execute(provider=provider, http_client=http_client, body=body)
                except (ProviderError, httpx.RequestError, httpx.HTTPStatusError) as e:
                    span.record_exception(e)
                    last_exception = e
                    continue

        raise NoAvailableProviderError("All providers in fallback chain failed.") from last_exception

    async def stream_with_fallback(
        self, http_client: httpx.AsyncClient, body: Dict[str, Any]
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        model = body.get("model")
        initial_chain = self.routing_policy.get_fallback_chain(model)
        if not initial_chain:
            raise NoAvailableProviderError(f"No provider configured for model '{model}'.")

        execution_chain = initial_chain
        specific_provider_name = body.get("provider")

        if specific_provider_name and specific_provider_name in self.providers:
            preferred_provider = self.providers[specific_provider_name]
            others = [p for p in initial_chain if p.name != specific_provider_name]
            execution_chain = [preferred_provider] + others

        stream_check_coroutines = [
            p.has_capability(model, ModelCapability.CHAT_STREAM, http_client, settings.provider.timeout)
            for p in execution_chain
        ]
        stream_check_results = await asyncio.gather(*stream_check_coroutines, return_exceptions=True)
        stream_capable_chain = [p for i, p in enumerate(execution_chain) if stream_check_results[i] is True]

        healthy_execution_chain = await self._get_healthy_fallback_chain(stream_capable_chain)
        if not healthy_execution_chain:
            raise NoAvailableProviderError("All streaming providers are currently unavailable.")

        for provider in healthy_execution_chain:
            try:
                async for chunk in self.executor.execute_stream(provider=provider, http_client=http_client, body=body):
                    yield chunk
                return
            except (ProviderError, httpx.RequestError, httpx.HTTPStatusError) as e:
                logger.warning("Provider stream failed", provider=provider.name, error=str(e))
                continue

        raise NoAvailableProviderError("All providers failed for streaming.")