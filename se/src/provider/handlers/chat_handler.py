from dataclasses import replace
from typing import Any, AsyncGenerator, Dict

import httpx
import structlog
from opentelemetry import trace

from ...application.assets.generated import (
    GeneratedAssetCanonicalizer,
    GeneratedAssetStreamAssembler,
)
from ...application.user_inference_quota import (
    InferenceQuotaAdmission,
    InferenceQuotaContext,
    NormalizedInferenceUsage,
    UserInferenceQuotaService,
)
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

    def __init__(
        self,
        *args,
        inference_quota: UserInferenceQuotaService | None = None,
        generated_asset_canonicalizer: GeneratedAssetCanonicalizer | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.inference_quota = inference_quota
        # The response-side F7 fence is always installed. In degraded mode the
        # unavailable sentinel passes ordinary text responses through and
        # terminally rejects generated media after provider success.
        self.generated_asset_canonicalizer = (
            generated_asset_canonicalizer
            if generated_asset_canonicalizer is not None
            else GeneratedAssetCanonicalizer.unavailable()
        )

    async def _reserve_inference_quota(
        self,
        *,
        body: Dict[str, Any],
        quota_context: InferenceQuotaContext | None,
        streaming_mode: bool,
    ) -> InferenceQuotaAdmission | None:
        """Complete UBQ admission before any provider-owned budget/work."""

        quota = self.inference_quota
        if quota is None or not quota.enabled:
            return None
        return await quota.reserve(
            context=quota_context,
            body=body,
            streaming_mode=streaming_mode,
        )

    @staticmethod
    def _unknown_ambiguous_usage(
        usage: NormalizedInferenceUsage,
    ) -> NormalizedInferenceUsage:
        """Retain attribution while refusing attempt-local resource undercount."""

        return replace(
            usage,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            compute_units=None,
            cost_usd=None,
        )

    @staticmethod
    def _has_stream_semantic_or_finish_progression(chunk: Any) -> bool:
        """Detect response progression that revokes an earlier terminal claim."""

        for choice in getattr(chunk, "choices", None) or []:
            if getattr(choice, "finish_reason", None) is not None:
                return True
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            if getattr(delta, "content", None):
                return True
            if getattr(delta, "reasoning_content", None):
                return True
            if getattr(delta, "tool_calls", None):
                return True

        metadata = getattr(chunk, "metadata", None)
        return bool(getattr(metadata, "content_parts", None))

    @classmethod
    def _is_trailing_usage_only_chunk(cls, chunk: Any) -> bool:
        """Prove post-terminal usage carries no semantic/finish progression."""

        return not cls._has_stream_semantic_or_finish_progression(chunk)

    async def _settle_inference_quota_success(
        self,
        *,
        admission: InferenceQuotaAdmission | None,
        usage: NormalizedInferenceUsage,
        executor_dispatch_count: int,
        call_budget: Any,
    ) -> None:
        """Settle one logical success without treating attempt usage as whole-call."""

        if admission is None:
            return
        quota = self.inference_quota
        if quota is None or not quota.enabled:
            raise RuntimeError(
                "UBQ-4 admission exists without active quota settlement authority"
            )

        settlement_usage = usage
        if (
            executor_dispatch_count != 1
            or int(getattr(call_budget, "retries_used", 0)) != 0
        ):
            settlement_usage = self._unknown_ambiguous_usage(usage)

        # Quota errors deliberately remain outside ProviderError/httpx
        # families so they cannot trigger provider fallback/breaker failure.
        await quota.settle_success(admission, settlement_usage)

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

    def _asset_attempt_requires_projection(
        self,
        body: Dict[str, Any],
    ) -> bool:
        hook = self.asset_projection_hook
        predicate = getattr(hook, "contains_canonical_assets", None)
        return bool(hook is not None and callable(predicate) and predicate(body))

    async def _project_asset_attempt(
        self,
        *,
        provider: Any,
        body: Dict[str, Any],
        owner_user_id: str | None,
        call_budget: Any,
    ) -> Dict[str, Any]:
        hook = self.asset_projection_hook
        project = getattr(hook, "project_attempt", None)
        if not callable(project):
            raise RuntimeError(
                "CAS-F5-D asset projection hook is not callable."
            )
        async def run_projection(_remaining: float):
            return await project(
                provider=provider,
                body=body,
                owner_user_id=owner_user_id,
            )

        result = await self._await_provider_operation_with_budget(
            run_projection,
            call_budget=call_budget,
            provider_name=provider.name,
            timeout_message=(
                "Provider asset projection deadline exceeded."
            ),
        )
        if not getattr(result, "engaged", False):
            raise RuntimeError(
                "CAS-F5-D asset-bearing attempt did not engage projection."
            )
        projected_body = getattr(result, "body", None)
        if not isinstance(projected_body, dict):
            raise RuntimeError(
                "CAS-F5-D projection returned an invalid request copy."
            )
        return projected_body

    async def execute_with_fallback(
        self,
        http_client: httpx.AsyncClient,
        body: Dict[str, Any],
        *,
        deadline_monotonic: float | None = None,
        owner_user_id: str | None = None,
        quota_context: InferenceQuotaContext | None = None,
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

        admission = await self._reserve_inference_quota(
            body=body,
            quota_context=quota_context,
            streaming_mode=False,
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
        executor_dispatch_count = 0

        for provider in healthy_execution_chain:
            with tracer.start_as_current_span(
                f"provider_attempt:{provider.name}"
            ) as span:
                span.set_attribute("provider.name", provider.name)
                asset_attempt_terminal = False
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
                    asset_attempt_terminal = (
                        self._asset_attempt_requires_projection(body)
                    )
                    if asset_attempt_terminal:
                        attempt_body = await self._project_asset_attempt(
                            provider=provider,
                            body=body,
                            owner_user_id=owner_user_id,
                            call_budget=call_budget,
                        )

                    executor_dispatch_count += 1
                    response = await self.executor.execute(
                        provider=provider,
                        http_client=http_client,
                        body=attempt_body,
                        timeout=self.timeout,
                        call_budget=call_budget,
                    )
                    quota = self.inference_quota
                    if admission is not None and quota is not None:
                        normalized_usage = quota.normalize_gateway_usage(response)
                        await self._settle_inference_quota_success(
                            admission=admission,
                            usage=normalized_usage,
                            executor_dispatch_count=executor_dispatch_count,
                            call_budget=call_budget,
                        )

                    # CAS-F7-P1 begins only after provider SUCCESS. Errors from
                    # this boundary are deliberately outside the provider
                    # fallback/circuit-breaker exception classes below.
                    return await self.generated_asset_canonicalizer.canonicalize(
                        response,
                        owner_user_id=owner_user_id,
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
                    if asset_attempt_terminal:
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
        quota_context: InferenceQuotaContext | None = None,
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

        admission = await self._reserve_inference_quota(
            body=body,
            quota_context=quota_context,
            streaming_mode=True,
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
        executor_dispatch_count = 0

        for provider in healthy_execution_chain:
            stream_started = False
            provider_stream = None
            stream_assembler = None
            asset_attempt_terminal = False
            normalized_stream_usage: NormalizedInferenceUsage | None = None
            terminal_seen = False
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
                asset_attempt_terminal = (
                    self._asset_attempt_requires_projection(body)
                )
                if asset_attempt_terminal:
                    attempt_body = await self._project_asset_attempt(
                        provider=provider,
                        body=body,
                        owner_user_id=owner_user_id,
                        call_budget=call_budget,
                    )

                executor_dispatch_count += 1
                provider_stream = self.executor.execute_stream(
                    provider=provider,
                    http_client=http_client,
                    body=attempt_body,
                    timeout=self.timeout,
                    call_budget=call_budget,
                )
                stream_assembler = GeneratedAssetStreamAssembler(
                    self.generated_asset_canonicalizer,
                    owner_user_id=owner_user_id,
                )
                async for chunk in provider_stream:
                    quota = self.inference_quota
                    raw_usage = getattr(chunk, "usage", None)
                    chunk_choices = getattr(chunk, "choices", None) or []
                    chunk_terminal = bool(chunk_choices) and all(
                        getattr(choice, "finish_reason", None) is not None
                        for choice in chunk_choices
                    )
                    semantic_or_finish_progression = (
                        self._has_stream_semantic_or_finish_progression(chunk)
                    )

                    # A finish marker only makes usage a provisional candidate.
                    # Later semantic/finish progression proves that earlier
                    # terminality was not final for the whole logical stream.
                    if terminal_seen and semantic_or_finish_progression:
                        terminal_seen = False
                    if (
                        normalized_stream_usage is not None
                        and semantic_or_finish_progression
                    ):
                        normalized_stream_usage = None

                    trusted_usage_evidence = (
                        raw_usage is not None
                        and (
                            chunk_terminal
                            or (
                                terminal_seen
                                and self._is_trailing_usage_only_chunk(chunk)
                            )
                        )
                    )
                    if (
                        admission is not None
                        and quota is not None
                        and trusted_usage_evidence
                    ):
                        metadata = getattr(chunk, "metadata", None)
                        normalized_stream_usage = quota.normalize_stream_usage(
                            usage=raw_usage,
                            provider=(
                                getattr(metadata, "provider", None)
                                or provider.name
                            ),
                            model=(
                                getattr(chunk, "model", None)
                                or (
                                    str(model)
                                    if model is not None
                                    else None
                                )
                            ),
                        )
                    terminal_seen = terminal_seen or chunk_terminal
                    public_chunk = stream_assembler.observe(chunk)
                    if public_chunk is None:
                        continue
                    stream_started = True
                    yield public_chunk

                quota = self.inference_quota
                if admission is not None and quota is not None:
                    if normalized_stream_usage is None:
                        normalized_stream_usage = quota.normalize_stream_usage(
                            usage=None,
                            provider=provider.name,
                            model=(
                                str(model)
                                if model is not None
                                else None
                            ),
                        )
                    await self._settle_inference_quota_success(
                        admission=admission,
                        usage=normalized_stream_usage,
                        executor_dispatch_count=executor_dispatch_count,
                        call_budget=call_budget,
                    )

                canonical_chunk = await stream_assembler.finalize()
                if canonical_chunk is not None:
                    stream_started = True
                    yield canonical_chunk
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
                if (
                    stream_started
                    or asset_attempt_terminal
                    or bool(
                        stream_assembler is not None
                        and stream_assembler.media_seen
                    )
                ):
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

