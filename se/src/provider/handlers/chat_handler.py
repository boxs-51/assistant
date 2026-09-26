import asyncio
from typing import Any, AsyncGenerator, Dict

import httpx
import structlog
from opentelemetry import trace

from ...application.assets.projection import AssetProjectionError
from ...domain.schemas import GatewayResponse, GatewayStreamChunk, ModelCapability
from ...domain.schemas.message import contains_canonical_asset_content
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

    def __init__(
        self,
        *args,
        asset_projector=None,
        asset_projection_enabled: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.asset_projector = asset_projector
        self.asset_projection_enabled = bool(asset_projection_enabled)

    def _asset_hook_engaged(self, body: Dict[str, Any]) -> bool:
        return bool(
            self.asset_projection_enabled
            and self.asset_projector is not None
            and contains_canonical_asset_content(body.get("messages", []))
        )

    async def _project_asset_attempt(
        self,
        *,
        body: Dict[str, Any],
        provider: Any,
        owner_user_id: str | None,
        call_budget: Any,
    ) -> Dict[str, Any]:
        if not isinstance(owner_user_id, str) or not owner_user_id.strip():
            raise AssetProjectionError(
                "CAS-F5-D asset projection requires trusted authenticated owner identity.",
                provider_name=getattr(provider, "name", None),
            )
        remaining = self._remaining_timeout(
            call_budget,
            provider_name=getattr(provider, "name", None),
        )
        try:
            projected = await asyncio.wait_for(
                self.asset_projector.project(
                    body=body,
                    owner_user_id=owner_user_id,
                    provider=provider,
                ),
                timeout=remaining,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderDeadlineExceededError(
                "Provider asset hydration/projection deadline exceeded.",
                provider_name=getattr(provider, "name", None),
            ) from exc
        return projected.body

    async def _has_required_capabilities(
        self,
        provider: Any,
        *,
        model: Any,
        http_client: httpx.AsyncClient,
        call_budget: Any,
        base_capability: ModelCapability,
        tools_present: bool,
    ) -> bool:
        """Evaluate request-scoped capability conjunction on one R10 budget."""

        if not await self._probe_capability_with_budget(
            provider=provider,
            model=model,
            capability=base_capability,
            http_client=http_client,
            call_budget=call_budget,
        ):
            return False

        if not tools_present:
            return True

        return await self._probe_capability_with_budget(
            provider=provider,
            model=model,
            capability=ModelCapability.TOOL_CALLING,
            http_client=http_client,
            call_budget=call_budget,
        )

    async def execute_with_fallback(
        self,
        http_client: httpx.AsyncClient,
        body: Dict[str, Any],
        *,
        deadline_monotonic: float | None = None,
        owner_user_id: str | None = None,
    ) -> GatewayResponse:
        model = body.get("model")
        tools_present = bool(body.get("tools"))

        execution_chain = self.routing_policy.get_fallback_chain(
            model=model,
            metadata=body.get("metadata"),
        )
        if not execution_chain:
            raise NoAvailableProviderError(
                f"No available or valid provider configured for model '{model}'."
            )

        call_budget = self._new_call_budget(deadline_monotonic)
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
            asset_hook_entered = False
            with tracer.start_as_current_span(
                f"provider_attempt:{provider.name}"
            ) as span:
                span.set_attribute("provider.name", provider.name)
                try:
                    if not await self._has_required_capabilities(
                        provider,
                        model=model,
                        http_client=http_client,
                        call_budget=call_budget,
                        base_capability=ModelCapability.CHAT,
                        tools_present=tools_present,
                    ):
                        continue

                    attempt_body = body
                    asset_hook_entered = self._asset_hook_engaged(body)
                    if asset_hook_entered:
                        attempt_body = await self._project_asset_attempt(
                            body=body,
                            provider=provider,
                            owner_user_id=owner_user_id,
                            call_budget=call_budget,
                        )

                    return await self.executor.execute(
                        provider=provider,
                        http_client=http_client,
                        body=attempt_body,
                        timeout=self.timeout,
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
                    if asset_hook_entered:
                        if last_detail is error:
                            raise
                        raise last_detail from error
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
        *,
        deadline_monotonic: float | None = None,
        owner_user_id: str | None = None,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Stream with fallback allowed only before the first visible chunk."""

        model = body.get("model")
        tools_present = bool(body.get("tools"))

        execution_chain = self.routing_policy.get_fallback_chain(
            model=model,
            metadata=body.get("metadata"),
        )
        if not execution_chain:
            raise NoAvailableProviderError(
                f"No available or valid provider configured for model '{model}'."
            )

        call_budget = self._new_call_budget(deadline_monotonic)
        healthy_execution_chain = await self._get_healthy_fallback_chain(
            execution_chain
        )
        if not healthy_execution_chain:
            raise NoAvailableProviderError(
                "All streaming providers are currently unavailable."
            )

        last_exception: Exception | None = None
        last_detail: ProviderError | None = None
        last_provider_name: str | None = None

        for provider in healthy_execution_chain:
            stream_started = False
            provider_stream = None
            asset_hook_entered = False
            try:
                if not await self._has_required_capabilities(
                    provider,
                    model=model,
                    http_client=http_client,
                    call_budget=call_budget,
                    base_capability=ModelCapability.CHAT_STREAM,
                    tools_present=tools_present,
                ):
                    continue

                attempt_body = body
                asset_hook_entered = self._asset_hook_engaged(body)
                if asset_hook_entered:
                    attempt_body = await self._project_asset_attempt(
                        body=body,
                        provider=provider,
                        owner_user_id=owner_user_id,
                        call_budget=call_budget,
                    )

                provider_stream = self.executor.execute_stream(
                    provider=provider,
                    http_client=http_client,
                    body=attempt_body,
                    timeout=self.timeout,
                    call_budget=call_budget,
                )
                async for chunk in provider_stream:
                    stream_started = True
                    yield chunk
                return

            except ProviderDeadlineExceededError:
                raise

            except (
                ProviderError,
                httpx.RequestError,
                httpx.HTTPStatusError,
            ) as error:
                logger.warning(
                    "Provider stream failed",
                    provider=provider.name,
                    error=str(error),
                    stream_started=stream_started,
                )
                detail = wrap_provider_exception(
                    error,
                    provider.name,
                )
                if asset_hook_entered or stream_started:
                    if detail is error:
                        raise
                    raise detail from error

                last_exception = error
                last_detail = detail
                last_provider_name = provider.name
                continue

            finally:
                if provider_stream is not None:
                    aclose = getattr(provider_stream, "aclose", None)
                    if callable(aclose):
                        try:
                            await aclose()
                        except Exception as cleanup_error:
                            logger.warning(
                                "Provider stream cleanup failed in handler.",
                                provider=provider.name,
                                error=str(cleanup_error),
                                error_type=type(cleanup_error).__name__,
                            )

        try:
            self._remaining_timeout(call_budget)
        except ProviderDeadlineExceededError:
            raise ProviderDeadlineExceededError(
                "Provider stream deadline exhausted during fallback."
            ) from last_exception

        detail = last_detail or (
            last_exception
            if isinstance(last_exception, ProviderError)
            else None
        )
        final_error = NoAvailableProviderError(
            "All providers failed before streaming output started.",
            provider_name=(
                getattr(detail, "provider_name", None)
                or last_provider_name
            ),
            status_code=getattr(detail, "status_code", None),
            error_code=getattr(detail, "error_code", None),
        )
        if last_exception is None:
            raise final_error
        raise final_error from last_exception

