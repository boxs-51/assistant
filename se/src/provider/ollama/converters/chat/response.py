import uuid
import time
import json
import re
import httpx
from typing import AsyncGenerator

import structlog
logger = structlog.get_logger(__name__)

from ....exceptions import ResponseValidationError
from ....core.tool_contract import ProviderToolNameMap
from .....domain.schemas import (
    GatewayResponse,
    GatewayChoice,
    GatewayMessage,
    GatewayUsage,
    GatewayStreamChunk,
    GatewayStreamChoice,
    GatewayStreamDelta,
    GatewayToolCall,
    FunctionCall,
    ResponseMetaData
)


class ResponseChats:

    @staticmethod
    def _gateway_usage_from_counts(payload: dict) -> GatewayUsage:
        usage_kwargs: dict[str, int] = {}
        if "prompt_eval_count" in payload:
            usage_kwargs["prompt_tokens"] = payload["prompt_eval_count"]
        if "eval_count" in payload:
            usage_kwargs["completion_tokens"] = payload["eval_count"]
        if (
            "prompt_tokens" in usage_kwargs
            and "completion_tokens" in usage_kwargs
        ):
            usage_kwargs["total_tokens"] = (
                usage_kwargs["prompt_tokens"]
                + usage_kwargs["completion_tokens"]
            )
        return GatewayUsage(**usage_kwargs)

    async def adapt_chat(
        self,
        response: httpx.Response,
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> GatewayResponse:
        """Chuyển đổi response JSON từ Ollama về GatewayResponse và bóc tách <think>."""
        try:
            response_data = response.json()
            message_data = response_data.get("message", {})
            raw_content = message_data.get("content", "").replace("\u2581", " ")

            # 1. Bóc tách khối <think>...</think> thành reasoning_content
            reasoning_content = None
            clean_content = raw_content

            think_match = re.search(r"<think>(.*?)</think>", raw_content, flags=re.DOTALL)
            if think_match:
                reasoning_content = think_match.group(1).strip()
                clean_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL).lstrip()

            # 2. Trích xuất Tool Calls
            tool_calls = None
            if "tool_calls" in message_data and message_data["tool_calls"]:
                tool_calls = []
                for tc in message_data["tool_calls"]:
                    func = tc.get("function", {})
                    args = func.get("arguments", {})
                    args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)

                    provider_name = func.get("name", "")
                    logical_name = (
                        tool_names.logical_name(provider_name)
                        if tool_names is not None and provider_name
                        else provider_name
                    )
                    tool_calls.append(
                        GatewayToolCall(
                            id=f"call_{uuid.uuid4().hex[:8]}",
                            type="function",
                            function=FunctionCall(
                                name=logical_name,
                                arguments=args_str
                            )
                        )
                    )

            finish_reason = "tool_calls" if tool_calls else ("stop" if response_data.get("done") else None)

            # 3. Preserve source-field presence for UBQ normalization.
            usage = self._gateway_usage_from_counts(response_data)

            msg_obj = GatewayMessage(
                role=message_data.get("role", "assistant"),
                content=clean_content,
                tool_calls=tool_calls
            )
            
            # Gán reasoning_content nếu GatewayMessage schema hỗ trợ
            if hasattr(msg_obj, "reasoning_content") and reasoning_content:
                msg_obj.reasoning_content = reasoning_content

            return GatewayResponse(
                id=f"chatcmpl-{uuid.uuid4()}",
                model=response_data.get("model", "default"),
                choices=[
                    GatewayChoice(
                        index=0,
                        message=msg_obj,
                        finish_reason=finish_reason,
                    )
                ],
                usage=usage,
                metadata= ResponseMetaData(
                    provider = "ollama",
                    raw_response = response_data,
                )
            )
        except ResponseValidationError:
            raise
        except (KeyError, IndexError, AttributeError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.error("Hỏng cấu trúc response từ Ollama:", error=str(e), response=response.text)
            raise ResponseValidationError(
                f"Invalid response structure from Ollama: {str(e)}", 
                provider_name="ollama"
            ) from e

    async def adapt_chat_stream(
        self,
        response: httpx.Response,
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """Chuyển đổi stream của Ollama sang GatewayStreamChunk và bóc tách <think> thời gian thực."""
        stream_id = f"chatcmpl-{uuid.uuid4()}"
        is_thinking = False

        async for line in response.aiter_lines():
            if not line:
                continue
            try:
                ollama_chunk = json.loads(line)
                message_chunk = ollama_chunk.get("message", {})
                is_done = ollama_chunk.get("done", False)

                gateway_usage = None
                if is_done:
                    gateway_usage = self._gateway_usage_from_counts(
                        ollama_chunk
                    )

                tool_calls = None
                if "tool_calls" in message_chunk and message_chunk["tool_calls"]:
                    tool_calls = []
                    for tc in message_chunk["tool_calls"]:
                        func = tc.get("function", {})
                        args = func.get("arguments", {})
                        args_str = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)

                        provider_name = func.get("name", "")
                        logical_name = (
                            tool_names.logical_name(provider_name)
                            if tool_names is not None and provider_name
                            else provider_name
                        )
                        tool_calls.append(
                            GatewayToolCall(
                                id=f"call_{uuid.uuid4().hex[:8]}",
                                type="function",
                                function=FunctionCall(
                                    name=logical_name,
                                    arguments=args_str
                                )
                            )
                        )

                finish_reason = "tool_calls" if tool_calls else ("stop" if is_done else None)
                raw_delta = message_chunk.get("content", "")

                # State Machine xử lý bóc tách <think> và </think> trong stream
                content_delta = None
                reasoning_delta = None
                if raw_delta:
                    raw_delta = raw_delta.replace("\u2581", " ")
                    if "<think>" in raw_delta:
                        is_thinking = True
                        raw_delta = raw_delta.replace("<think>", "")
                    
                    if "</think>" in raw_delta:
                        is_thinking = False
                        parts = raw_delta.split("</think>")
                        reasoning_delta = parts[0] if parts[0] else None
                        content_delta = parts[1].lstrip() if len(parts) > 1 and parts[1] else None
                    else:
                        if is_thinking:
                            reasoning_delta = raw_delta
                        else:
                            content_delta = raw_delta

                delta_obj = GatewayStreamDelta(
                    role="assistant",
                    content=content_delta,
                    tool_calls=tool_calls
                )
                if hasattr(delta_obj, "reasoning_content") and reasoning_delta:
                    delta_obj.reasoning_content = reasoning_delta

                yield GatewayStreamChunk(
                    id=stream_id,
                    model=ollama_chunk.get("model", "default"),
                    choices=[GatewayStreamChoice(
                        index=0,
                        delta=delta_obj,
                        finish_reason=finish_reason
                    )],
                    metadata=ResponseMetaData(
                        provider="ollama",
                    ),
                    usage=gateway_usage
                )
            except ResponseValidationError:
                raise
            except (KeyError, IndexError, AttributeError, TypeError, ValueError, json.JSONDecodeError) as e:
                logger.warning("Failed to normalize chunk from Ollama stream", error=str(e))
                raise ResponseValidationError(
                    f"Invalid stream chunk from Ollama: {e}",
                    provider_name="ollama",
                ) from e
