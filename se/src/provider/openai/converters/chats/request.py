from copy import deepcopy
import json
from typing import Any, Dict, Mapping

from ....core.tool_contract import (
    ProviderToolNameMap,
    ProviderToolContractError,
    normalize_provider_tool_schema,
)
from ....core.tool_request_context import build_request_tool_name_map


class RequestChats:

    @staticmethod
    def _tool_dict(tool: Any) -> dict[str, Any]:
        if isinstance(tool, Mapping):
            return deepcopy(dict(tool))
        if hasattr(tool, "model_dump"):
            return deepcopy(tool.model_dump(mode="python"))
        raise ProviderToolContractError(
            f"unsupported OpenAI tool definition: {type(tool).__name__}"
        )

    @staticmethod
    def _arguments_json(arguments: Any) -> str:
        if isinstance(arguments, str):
            return arguments
        if isinstance(arguments, (dict, list)):
            return json.dumps(arguments, ensure_ascii=False)
        raise ProviderToolContractError(
            "OpenAI assistant tool-call arguments must be a JSON string/object"
        )

    def adapt_chat_request(
        self,
        request: Dict[str, Any],
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> Dict[str, Any]:
        """Lower the neutral Gateway request to OpenAI Chat Completions."""

        adapted_request = deepcopy(request)
        names = tool_names or build_request_tool_name_map("openai", adapted_request)

        config = adapted_request.pop("config", {}) or {}
        adapted_request.pop("metadata", None)
        adapted_request.pop("session_id", None)
        adapted_request.pop("connection_id", None)
        adapted_request.pop("agent_enabled", None)
        adapted_request.pop("agent_id", None)

        for field in (
            "temperature",
            "top_p",
            "max_tokens",
            "presence_penalty",
            "frequency_penalty",
            "response_format",
        ):
            if config.get(field) is not None:
                adapted_request[field] = config[field]
        if "stream" in config:
            adapted_request["stream"] = bool(config["stream"])

        tools = adapted_request.get("tools")
        if tools:
            native_tools = []
            for raw_tool in tools:
                tool = self._tool_dict(raw_tool)
                logical_name = tool.get("name")
                if not isinstance(logical_name, str) or not logical_name:
                    raise ProviderToolContractError(
                        "OpenAI tool definition requires a non-empty logical name"
                    )

                function: dict[str, Any] = {
                    "name": names.provider_name(logical_name),
                    "description": str(tool.get("description") or ""),
                }
                parameters = normalize_provider_tool_schema(
                    "openai", tool.get("parameters")
                )
                if parameters is not None:
                    function["parameters"] = parameters

                native_tools.append({"type": "function", "function": function})
            adapted_request["tools"] = native_tools

        for message in adapted_request.get("messages") or []:
            if not isinstance(message, dict):
                continue

            if message.get("role") == "assistant":
                tool_calls = message.get("tool_calls") or []
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if not isinstance(call, dict):
                            continue
                        function = call.get("function")
                        if not isinstance(function, dict):
                            continue
                        logical_name = function.get("name")
                        if isinstance(logical_name, str) and logical_name:
                            function["name"] = names.provider_name(logical_name)
                        if "arguments" in function:
                            function["arguments"] = self._arguments_json(
                                function["arguments"]
                            )

                function_call = message.get("function_call")
                if isinstance(function_call, dict):
                    logical_name = function_call.get("name")
                    if isinstance(logical_name, str) and logical_name:
                        function_call["name"] = names.provider_name(logical_name)
                    if "arguments" in function_call:
                        function_call["arguments"] = self._arguments_json(
                            function_call["arguments"]
                        )

            if message.get("role") == "tool":
                # Chat Completions identifies tool-result messages by
                # tool_call_id; neutral Gateway name is not a wire field.
                message.pop("name", None)
                message.pop("tool_name", None)
                content = message.get("content")
                if content is not None and not isinstance(content, (str, list)):
                    message["content"] = json.dumps(content, ensure_ascii=False)

        return adapted_request
