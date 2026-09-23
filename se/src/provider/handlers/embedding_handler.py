from typing import Any, Dict

import httpx

from ...domain.schemas import ModelCapability
from ..exceptions import (
    NoAvailableProviderError,
    ProviderDeadlineExceededError,
)
from .base import BaseExecutionHandler


class EmbeddingExecutionHandler(BaseExecutionHandler):
    """Create embeddings with one logical budget across fallback providers."""

    async def execute(
        self,
        http_client: httpx.AsyncClient,
        body: Dict[str, Any],
    ) -> Any:
        model = body.get("model")
        initial_chain = self.routing_policy.get_fallback_chain(model)
        if not initial_chain:
            raise NoAvailableProviderError(
                f"No provider configured for model '{model}'."
            )

        call_budget = self._new_call_budget()
        healthy_chain = await self._get_healthy_fallback_chain(initial_chain)
        if not healthy_chain:
            raise NoAvailableProviderError(
                "All embedding providers are currently unavailable."
            )

        last_error: Exception | None = None

        for provider in healthy_chain:
            try:
                probe_timeout = self._remaining_timeout(
                    call_budget,
                    provider_name=provider.name,
                )
                if not await provider.has_capability(
                    model,
                    ModelCapability.EMBEDDINGS,
                    http_client,
                    probe_timeout,
                ):
                    continue

                return await self.executor.execute_generic(
                    provider=provider,
                    execution_callable=(
                        lambda attempt_timeout=self.timeout, p=provider:
                        p.embeddings.embeddings(
                            http_client=http_client,
                            body=body,
                            timeout=attempt_timeout,
                        )
                    ),
                    timeout=self.timeout,
                    call_budget=call_budget,
                )
            except ProviderDeadlineExceededError:
                raise
            except Exception as exc:
                last_error = exc
                continue

        try:
            self._remaining_timeout(call_budget)
        except ProviderDeadlineExceededError as deadline_error:
            raise ProviderDeadlineExceededError(
                "Provider call deadline exhausted during embedding fallback."
            ) from last_error

        final_error = NoAvailableProviderError(
            "All embedding providers are unavailable or unsupported.",
            provider_name=getattr(last_error, "provider_name", None),
            status_code=getattr(last_error, "status_code", None),
            error_code=getattr(last_error, "error_code", None),
        )
        if last_error is None:
            raise final_error
        raise final_error from last_error
