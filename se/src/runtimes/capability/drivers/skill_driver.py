from __future__ import annotations

import json
from typing import Any, Mapping

from ....application.user_inference_quota import derive_skill_inference_request_id
from ...agent.contracts.inference import InferenceMessage, InferenceRequest
from ..contracts.context import CapabilityExecutionContext
from .base import BaseCapabilityDriver


class ExecutableSkillCapabilityDriver(BaseCapabilityDriver):
    """One-shot server skill backed by the canonical inference port."""

    def __init__(self, definition, instruction: str, inference: Any):
        super().__init__(definition)
        self._instruction = instruction
        self._inference = inference

    async def execute(
        self,
        context: CapabilityExecutionContext,
        arguments: Mapping[str, Any],
    ) -> Any:
        prompt = arguments.get("prompt")
        if prompt is None:
            prompt = json.dumps(dict(arguments), ensure_ascii=False)
        response = await self._inference.complete(
            InferenceRequest(
                request_id=derive_skill_inference_request_id(context.invocation_id),
                execution_id=context.execution_id,
                iteration=1,
                messages=(
                    InferenceMessage(role="system", content=self._instruction),
                    InferenceMessage(role="user", content=prompt),
                ),
                model=context.metadata.get("model"),
                timeout_seconds=context.remaining_seconds,
                budget_identity=context.identity,
                cancellation_event=context.cancellation_event,
                metadata={
                    **context.metadata,
                    "quota_source_surface": "SKILL",
                    "outer_request_id": context.request_id,
                    "invocation_id": context.invocation_id,
                    **(
                        {"task_id": context.task_id}
                        if context.task_id is not None else {}
                    ),
                    **(
                        {"branch_id": context.branch_id}
                        if context.branch_id is not None else {}
                    ),
                    **(
                        {"correlation_id": context.correlation_id}
                        if context.correlation_id is not None else {}
                    ),
                    **({"trace_id": context.trace_id} if context.trace_id is not None else {}),
                },
            )
        )
        return {
            "message": response.message.model_dump(mode="json"),
            "usage": response.usage.model_dump(mode="json"),
            "provider": response.provider,
            "model": response.model,
        }
