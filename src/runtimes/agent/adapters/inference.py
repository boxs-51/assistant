from __future__ import annotations

import asyncio
from typing import Any

from ..contracts.context import AgentExecutionContext
from ..contracts.inference import (
    InferenceMessage,
    InferencePort,
    InferenceRequest,
    InferenceResponse,
    InferenceToolCall,
    InferenceToolDefinition,
    InferenceUsage,
)
from ..tool_execution.errors import ToolArgumentParseError
from .messages import inference_message_to_provider, jsonable


class ProviderInferenceAdapter(InferencePort):
    """Provider-neutral bridge over the existing ProviderRuntime."""

    def __init__(self, provider_runtime: Any, http_client: Any):
        self._provider_runtime = provider_runtime
        self._http_client = http_client

    @staticmethod
    def _serialize_tool(tool: InferenceToolDefinition | dict[str, Any]) -> dict[str, Any]:
        normalized = (
            tool
            if isinstance(tool, InferenceToolDefinition)
            else InferenceToolDefinition.model_validate(tool)
        )

        return {
            "name": normalized.name,
            "description": normalized.description,
            "parameters": jsonable(normalized.parameters),
        }
    @staticmethod
    def serialize_request(request: InferenceRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model or "",
            "messages": [
                inference_message_to_provider(message)
                for message in request.messages
            ],
            "tools": [
                ProviderInferenceAdapter._serialize_tool(tool)
                for tool in request.tools
            ],
            "metadata": jsonable(request.metadata),
        }
        config: dict[str, Any] = {}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            config["max_tokens"] = request.max_output_tokens
        if config:
            body["config"] = config
        return body

    async def complete(self, request: InferenceRequest) -> InferenceResponse:
        handler = getattr(self._provider_runtime, "chat_handler", None)
        if handler is None:
            raise RuntimeError("ProviderRuntime chat handler is not initialized.")

        body = self.serialize_request(request)
        provider_task = asyncio.create_task(
            handler.execute_with_fallback(self._http_client, body),
            name=f"inference:{request.execution_id}:{request.request_id}",
        )
        cancellation_task = None
        try:
            if request.cancellation_event is not None:
                if request.cancellation_event.is_set():
                    provider_task.cancel()
                    await asyncio.gather(provider_task, return_exceptions=True)
                    raise asyncio.CancelledError()
                cancellation_task = asyncio.create_task(
                    request.cancellation_event.wait(),
                    name=f"inference-cancel:{request.request_id}",
                )

            wait_set = {provider_task}
            if cancellation_task is not None:
                wait_set.add(cancellation_task)

            done, _ = await asyncio.wait(
                wait_set,
                timeout=request.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if provider_task in done:
                response = await provider_task
            elif cancellation_task is not None and cancellation_task in done:
                provider_task.cancel()
                await asyncio.gather(provider_task, return_exceptions=True)
                raise asyncio.CancelledError()
            else:
                provider_task.cancel()
                await asyncio.gather(provider_task, return_exceptions=True)
                raise asyncio.TimeoutError(
                    "Inference execution exceeded its timeout."
                )
        finally:
            if cancellation_task is not None:
                cancellation_task.cancel()
                await asyncio.gather(
                    cancellation_task,
                    return_exceptions=True,
                )

        if not response.choices:
            raise ValueError("Provider returned an empty choice list.")

        choice = response.choices[0]
        gateway_message = choice.message
        inference_tool_calls = []
        for call in gateway_message.tool_calls or []:
            inference_tool_calls.append(
                InferenceToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=self._parse_arguments(call.function.arguments),
                )
            )

        usage = getattr(response, "usage", None)
        inference_usage = InferenceUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            total_tokens=getattr(usage, "total_tokens", 0),
            tool_invocations=len(inference_tool_calls),
        )
        finish_reason = choice.finish_reason
        finish_reason = getattr(finish_reason, "value", finish_reason)

        return InferenceResponse(
            request_id=request.request_id,
            execution_id=request.execution_id,
            iteration=request.iteration,
            message=InferenceMessage(
                role=gateway_message.role,
                content=jsonable(gateway_message.content),
                tool_calls=inference_tool_calls,
                metadata=jsonable(
                    getattr(gateway_message, "metadata", {}) or {}
                ),
            ),
            finish_reason=finish_reason,
            usage=inference_usage,
            provider=response.provider,
            model=response.model,
            metadata=jsonable(getattr(response, "metadata", {}) or {}),
        )

    @staticmethod
    def _parse_arguments(value: Any) -> dict[str, Any]:
        import json
        if value in (None, ""):
            return {}
        if isinstance(value, dict):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ToolArgumentParseError(
                "Tool call arguments must contain valid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise ToolArgumentParseError(
                "Tool call arguments must decode to a JSON object."
            )
        return parsed