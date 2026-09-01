from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel

from ....domain.schemas.message import GatewayMessage
from ....domain.schemas.tool import GatewayToolCall
from ..contracts.inference import InferenceMessage, InferenceToolCall


def jsonable(value: Any) -> Any:
    """Recursively convert repository DTOs into JSON-safe Python values."""
    if isinstance(value, BaseModel):
        return jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def gateway_message_to_inference(
    message: GatewayMessage | Mapping[str, Any],
) -> InferenceMessage:
    raw = jsonable(message)
    tool_calls = []
    for call in raw.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            import json
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"raw_arguments": arguments}
        if not isinstance(arguments, dict):
            arguments = {"raw_arguments": arguments}
        tool_calls.append(
            InferenceToolCall(
                id=str(call.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=arguments,
            )
        )
    return InferenceMessage(
        role=str(raw.get("role") or ""),
        content=jsonable(raw.get("content")),
        tool_calls=tool_calls,
        name=raw.get("name"),
        tool_call_id=raw.get("tool_call_id"),
        metadata=jsonable(raw.get("metadata") or {}),
    )


def inference_message_to_provider(
    message: InferenceMessage | Mapping[str, Any],
) -> dict[str, Any]:
    raw = jsonable(message)
    raw["tool_calls"] = [
        {
            "id": str(call.get("id") or ""),
            "type": "function",
            "function": {
                "name": str(call.get("name") or ""),
                "arguments": __import__("json").dumps(
                    call.get("arguments") or {},
                    ensure_ascii=False,
                ),
            },
        }
        for call in raw.get("tool_calls", [])
    ]
    raw = {key: value for key, value in raw.items() if value is not None}
    return raw


def gateway_tool_call_to_inference(
    call: GatewayToolCall | Mapping[str, Any],
) -> InferenceToolCall:
    raw = jsonable(call)
    function = raw.get("function") or {}
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        import json
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"raw_arguments": arguments}
    if not isinstance(arguments, dict):
        arguments = {"raw_arguments": arguments}
    return InferenceToolCall(
        id=str(raw.get("id") or ""),
        name=str(function.get("name") or ""),
        arguments=arguments,
    )