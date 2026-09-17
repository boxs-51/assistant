from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

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
                request_id=context.request_id or f"skill_{uuid.uuid4().hex}",
                execution_id=context.execution_id,
                iteration=1,
                messages=(
                    InferenceMessage(role="system", content=self._instruction),
                    InferenceMessage(role="user", content=prompt),
                ),
                model=context.metadata.get("model"),
                timeout_seconds=context.remaining_seconds,
                cancellation_event=context.cancellation_event,
                metadata={**context.metadata, "invocation_id": context.invocation_id},
            )
        )
        return {
            "message": response.message.model_dump(mode="json"),
            "usage": response.usage.model_dump(mode="json"),
            "provider": response.provider,
            "model": response.model,
        }
