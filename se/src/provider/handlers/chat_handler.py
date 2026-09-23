import asyncio
from typing import Any, AsyncGenerator, Dict

import httpx
import structlog
from opentelemetry import trace

from ...domain.schemas import GatewayResponse, GatewayStreamChunk, ModelCapability
from ..exceptions import (
    NoAvailableProviderError,
    ProviderDeadlineExceededError,
    ProviderError,
    wrap_provider_exception,
)
from .base import BaseExecutionHandler

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


class ChatExecutionHandler(BaseExecutionHandler):
    """Execute chat requests with deterministic provider fallback."""

    async def execute_with_fallback(
        self,
        http_client: httpx.AsyncClient,
        body: Dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> GatewayResponse:
        model = body.get("model")

        execution_chain = self.routing_policy.get_fallback_chain(
            model=model,
            metadata=body.get("metadata"),
        )
        if not execution_chain:
            raise NoAvailableProviderError(
                f"No available or valid provider configured for model '{model}'."
            )

        effective_timeout = self._effective_call_timeout(timeout)
        call_budget = self._new_call_budget(effective_timeout)
        healthy_execution_chain = await self._get_healthy_fallback_chain(
            execution_chain
        )
        if not healthy_execution_chain:
            raise NoAvailableProviderError(
                "All providers are currently unavailable "
                "(circuit breakers open)."
            )

        last_exception: Exception | None = None
        last_detail: ProviderError | None = None
        last_provider_name: str | None = None

        for provider in healthy_execution_chain:
            with tracer.start_as_current_span(
                f"provider_attempt:{provider.name}"
            ) as span:
                span.set_attribute("provider.name", provider.name)
                try:
                    probe_timeout = self._remaining_timeout(
                        call_budget,
                        provider_name=provider.name,
                    )
                    if not await provider.has_capability(
                        model,
                        ModelCapability.CHAT,
                        http_client,
                        probe_timeout,
                    ):
                        continue

                    return await self.executor.execute(
                        provider=provider,
                        http_client=http_client,
                        body=body,
                        timeout=effective_timeout,
                        call_budget=call_budget,
                    )
                except ProviderDeadlineExceededError:
                    raise
                except (
                    ProviderError,
                    httpx.RequestError,
                    httpx.HTTPStatusError,
                ) as error:
                    span.record_exception(error)
                    last_exception = error
                    last_provider_name = provider.name
                    last_detail = wrap_provider_exception(
                        error,
                        provider.name,
                    )
                    continue

        try:
            self._remaining_timeout(call_budget)
        except ProviderDeadlineExceededError as deadline_error:
            raise ProviderDeadlineExceededError(
                "Provider call deadline exhausted during fallback."
            ) from last_exception

        detail = last_detail or (
            last_exception
            if isinstance(last_exception, ProviderError)
            else None
        )
        final_error = NoAvailableProviderError(
            "All providers in fallback chain failed.",
            provider_name=(
                getattr(detail, "provider_name", None)
                or last_provider_name
            ),
            status_code=getattr(
                detail,
                "status_code",
                None,
            ),
            error_code=getattr(
                detail,
                "error_code",
                None,
            ),
        )
        if last_exception is None:
            raise final_error
        raise final_error from last_exception

    async def stream_with_fallback(
        self,
        http_client: httpx.AsyncClient,
        body: Dict[str, Any],
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        model = body.get("model")

        execution_chain = self.routing_policy.get_fallback_chain(
            model=model,
            metadata=body.get("metadata"),
        )

        if not execution_chain:
            raise NoAvailableProviderError(
                f"No available or valid provider configured for model '{model}'."
            )

        stream_check_coroutines = [
            provider.has_capability(
                model,
                ModelCapability.CHAT_STREAM,
                http_client,
                self.timeout,
            )
            for provider in execution_chain
        ]
        stream_check_results = await asyncio.gather(
            *stream_check_coroutines,
            return_exceptions=True,
        )
        stream_capable_chain = [
            provider
            for index, provider in enumerate(execution_chain)
            if stream_check_results[index] is True
        ]

        healthy_execution_chain = await self._get_healthy_fallback_chain(
            stream_capable_chain
        )
        if not healthy_execution_chain:
            raise NoAvailableProviderError(
                "All streaming providers are currently unavailable."
            )

        for provider in healthy_execution_chain:
            stream_started = False
            try:
                async for chunk in self.executor.execute_stream(
                    provider=provider,
                    http_client=http_client,
                    body=body,
                ):
                    stream_started = True
                    yield chunk
                return
            except (
                ProviderError,
                httpx.RequestError,
                httpx.HTTPStatusError,
            ) as error:
                logger.warning(
                    "Provider stream failed",
                    provider=provider.name,
                    error=str(error),
                )
                if stream_started:
                    raise ProviderError(
                        f"Stream interrupted mid-response on provider "
                        f"{provider.name}"
                    ) from error
                continue

        raise NoAvailableProviderError(
            "All providers failed for streaming."
        )
