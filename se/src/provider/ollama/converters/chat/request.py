from copy import deepcopy
from pathlib import Path
import base64
import json
from typing import Any, Dict, List, Mapping, Optional

import structlog

from ....core.tool_contract import (
    ProviderToolContractError,
    ProviderToolNameMap,
    normalize_provider_tool_schema,
)
from ....core.tool_request_context import build_request_tool_name_map


logger = structlog.get_logger(__name__)

MAX_TEXT_LENGTH = 100_000


class RequestChats:
    def _extract_base64_image(self, part: Dict[str, Any]) -> Optional[str]:
        """Extract Base64 image data from Gateway/OpenAI-compatible parts."""

        if part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if ";base64," in url:
                return url.split(";base64,", 1)[1]

        if part.get("type") == "image":
            img_obj = part.get("image", {})
            attachment = img_obj.get("attachment", {})
            if attachment.get("base64_data"):
                return attachment.get("base64_data")

            path_str = attachment.get("path") or attachment.get("uri") or ""
            if path_str and Path(path_str).is_file():
                try:
                    with open(path_str, "rb") as handle:
                        return base64.b64encode(handle.read()).decode("utf-8")
                except Exception as exc:
                    logger.warning(
                        "Không thể đọc file ảnh local",
                        path=path_str,
                        error=str(exc),
                    )

        return None

    @staticmethod
    def _tool_dict(tool: Any) -> dict[str, Any]:
        if isinstance(tool, Mapping):
            return deepcopy(dict(tool))
        if hasattr(tool, "model_dump"):
            return deepcopy(tool.model_dump(mode="python"))
        raise ProviderToolContractError(
            f"unsupported Ollama tool definition: {type(tool).__name__}"
        )

    @staticmethod
    def _arguments_object(arguments: Any) -> dict[str, Any]:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ProviderToolContractError(
                    "Ollama assistant tool-call arguments must contain valid JSON"
                ) from exc
        if not isinstance(arguments, dict):
            raise ProviderToolContractError(
                "Ollama assistant tool-call arguments must be a JSON object"
            )
        return deepcopy(arguments)

    @staticmethod
    def _tool_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        return json.dumps(content, ensure_ascii=False)

    def adapt_chat_request(
        self,
        request: Dict[str, Any],
        stream: bool = False,
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> Dict[str, Any]:
        """Convert neutral Gateway request to Ollama /api/chat payload."""

        names = tool_names or build_request_tool_name_map("ollama", request)
        ollama_messages = []

        for msg in request.get("messages", []):
            role = msg.get("role")
            content = msg.get("content", "")
            images: List[str] = []
            text_parts: List[str] = []

            if role == "tool":
                logical_name = msg.get("name") or msg.get("tool_name")
                if not isinstance(logical_name, str) or not logical_name:
                    raise ProviderToolContractError(
                        "Ollama tool-result history requires a logical tool name"
                    )
                ollama_messages.append(
                    {
                        "role": "tool",
                        "tool_name": names.provider_name(logical_name),
                        "content": self._tool_content(content),
                    }
                )
                continue

            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "text":
                            text = part.get("text", "")
                            if isinstance(text, str):
                                text_parts.append(text)
                        else:
                            b64_img = self._extract_base64_image(part)
                            if b64_img:
                                images.append(b64_img)

            full_text = "\n".join(text_parts)
            if len(full_text) > MAX_TEXT_LENGTH:
                full_text = (
                    full_text[:MAX_TEXT_LENGTH]
                    + "\n...[Nội dung bị cắt bớt]..."
                )

            msg_obj: Dict[str, Any] = {
                "role": role,
                "content": full_text,
            }
            if images:
                msg_obj["images"] = images

            if role == "assistant" and msg.get("tool_calls"):
                native_calls = []
                for call in msg["tool_calls"]:
                    if not isinstance(call, Mapping):
                        raise ProviderToolContractError(
                            "Ollama assistant tool call must be an object"
                        )
                    function = call.get("function")
                    if not isinstance(function, Mapping):
                        raise ProviderToolContractError(
                            "Ollama assistant tool call requires function data"
                        )
                    logical_name = function.get("name")
                    if not isinstance(logical_name, str) or not logical_name:
                        raise ProviderToolContractError(
                            "Ollama assistant tool call requires a logical tool name"
                        )
                    native_calls.append(
                        {
                            "type": "function",
                            "function": {
                                "name": names.provider_name(logical_name),
                                "arguments": self._arguments_object(
                                    function.get("arguments", {})
                                ),
                            },
                        }
                    )
                msg_obj["tool_calls"] = native_calls

            ollama_messages.append(msg_obj)

        adapted_request: Dict[str, Any] = {
            "model": request.get("model"),
            "messages": ollama_messages,
            "stream": stream,
        }

        config = request.get("config", {})
        options: Dict[str, Any] = {}
        if config.get("temperature") is not None:
            options["temperature"] = config["temperature"]
        if config.get("top_p") is not None:
            options["top_p"] = config["top_p"]
        if config.get("max_tokens") is not None:
            options["num_predict"] = config["max_tokens"]
        if config.get("presence_penalty") is not None:
            options["presence_penalty"] = config["presence_penalty"]
        if config.get("frequency_penalty") is not None:
            options["frequency_penalty"] = config["frequency_penalty"]
        if config.get("stop") is not None:
            options["stop"] = config["stop"]
        if options:
            adapted_request["options"] = options

        if config.get("response_format") in {"json", "json_object"}:
            adapted_request["format"] = "json"

        tools = request.get("tools")
        if tools and isinstance(tools, list):
            native_tools = []
            for raw_tool in tools:
                tool = self._tool_dict(raw_tool)
                logical_name = tool.get("name")
                if not isinstance(logical_name, str) or not logical_name:
                    raise ProviderToolContractError(
                        "Ollama tool definition requires a logical tool name"
                    )

                function: dict[str, Any] = {
                    "name": names.provider_name(logical_name),
                    "description": str(tool.get("description") or ""),
                }
                parameters = normalize_provider_tool_schema(
                    "ollama", tool.get("parameters")
                )
                if parameters is not None:
                    function["parameters"] = parameters

                native_tools.append(
                    {
                        "type": "function",
                        "function": function,
                    }
                )
            adapted_request["tools"] = native_tools

        return adapted_request
