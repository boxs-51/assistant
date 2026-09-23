from copy import deepcopy
import httpx
import json

from typing import Any, AsyncGenerator

from ....exceptions import ResponseValidationError
from ....core.tool_contract import ProviderToolNameMap
from .....domain.schemas import GatewayResponse, GatewayStreamChunk


class ResponseChats:

    @staticmethod
    def _restore_tool_names(
        payload: dict[str, Any],
        tool_names: ProviderToolNameMap | None,
        *,
        message_key: str,
    ) -> None:
        if tool_names is None:
            return

        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get(message_key)
            if not isinstance(message, dict):
                continue
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                provider_name = function.get("name")
                if isinstance(provider_name, str) and provider_name:
                    function["name"] = tool_names.logical_name(provider_name)

    async def adapt_chat(
        self,
        response: httpx.Response,
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> GatewayResponse:
        """Convert OpenAI response and restore canonical logical tool names."""

        try:
            response_data = response.json()
            if not isinstance(response_data, dict):
                raise TypeError("response body must be a JSON object")

            raw_response = deepcopy(response_data)
            self._restore_tool_names(
                response_data,
                tool_names,
                message_key="message",
            )
            response_data["metadata"] = {
                "provider": "openai",
                "provider_response_id": response_data.get("id"),
                "raw_response": raw_response,
            }
            return GatewayResponse.model_validate(response_data)
        except ResponseValidationError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ResponseValidationError(
                f"Invalid response structure from OpenAI-compatible API: {exc}",
                provider_name="openai",
            ) from exc

    async def adapt_chat_stream(
        self,
        response: httpx.Response,
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Convert OpenAI SSE chunks and restore canonical tool names."""

        async for line in response.aiter_lines():
            line = (
                line.decode("utf-8").strip()
                if isinstance(line, bytes)
                else line.strip()
            )
            if not line.startswith("data: "):
                continue

            data = line[len("data: "):]
            if data == "[DONE]":
                break

            try:
                chunk_json = json.loads(data)
                if not isinstance(chunk_json, dict):
                    raise TypeError("stream chunk must be a JSON object")

                self._restore_tool_names(
                    chunk_json,
                    tool_names,
                    message_key="delta",
                )
                chunk_json["metadata"] = {
                    "provider": "openai",
                    "provider_response_id": chunk_json.get("id"),
                }
                yield GatewayStreamChunk.model_validate(chunk_json)
            except ResponseValidationError:
                raise
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ResponseValidationError(
                    f"Invalid stream chunk from OpenAI-compatible API: {exc}",
                    provider_name="openai",
                ) from exc
