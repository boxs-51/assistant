from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest

from se.src.provider.core.tool_contract import ProviderToolContractError
from se.src.provider.core.tool_request_context import build_request_tool_name_map
from se.src.provider.gemini.api.chats import GeminiChat
from se.src.provider.gemini.converters.chats.request import RequestChats as GeminiRequestChats
from se.src.provider.gemini.converters.chats.response import ResponseChats as GeminiResponseChats
from se.src.provider.ollama.api.chats import OllamaChats
from se.src.provider.ollama.converters.chat.request import RequestChats as OllamaRequestChats
from se.src.provider.ollama.converters.chat.response import ResponseChats as OllamaResponseChats
from se.src.provider.openai.api.chats import OpenAIChats
from se.src.provider.openai.converters.chats.request import RequestChats as OpenAIRequestChats
from se.src.provider.openai.converters.chats.response import ResponseChats as OpenAIResponseChats
from se.src.provider.exceptions import ResponseValidationError


def _response(*, payload: dict | None = None, content: bytes | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://provider.example/v1/chat")
    if payload is not None:
        return httpx.Response(200, json=payload, request=request)
    return httpx.Response(200, content=content or b"", request=request)


def _tool(name: str = "web.search") -> dict:
    return {
        "name": name,
        "description": "Search",
        "parameters": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def _two_turn_body(name: str = "web.search") -> dict:
    return {
        "model": "test-model",
        "tools": [_tool(name)],
        "messages": [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps({"query": "latest"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "name": name,
                "tool_call_id": "call-1",
                "content": {"items": [1]},
            },
        ],
    }


def test_request_tool_name_map_deduplicates_declarations_and_history():
    body = _two_turn_body()

    names = build_request_tool_name_map("openai", body)

    assert tuple(names.logical_to_provider) == ("web.search",)
    assert names.logical_name(names.provider_name("web.search")) == "web.search"


def test_openai_lowering_uses_native_envelope_alias_and_tool_call_id_without_mutation():
    body = _two_turn_body()
    original = deepcopy(body)
    names = build_request_tool_name_map("openai", body)

    prepared = OpenAIRequestChats().adapt_chat_request(body, tool_names=names)
    alias = names.provider_name("web.search")

    assert prepared["tools"] == [
        {
            "type": "function",
            "function": {
                "name": alias,
                "description": "Search",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    assert prepared["messages"][1]["tool_calls"][0]["function"]["name"] == alias
    assert isinstance(
        prepared["messages"][1]["tool_calls"][0]["function"]["arguments"], str
    )
    assert prepared["messages"][2]["tool_call_id"] == "call-1"
    assert "name" not in prepared["messages"][2]
    assert json.loads(prepared["messages"][2]["content"]) == {"items": [1]}
    assert body == original


def test_openai_prepare_request_regression_for_wrong_keyword_is_fixed():
    class Mapper:
        def translate(self, model):
            return model

    class Provider:
        mapper = Mapper()

    prepared = OpenAIChats(Provider()).prepare_request(_two_turn_body())

    assert prepared["tools"][0]["type"] == "function"


@pytest.mark.asyncio
async def test_openai_response_restores_logical_name_and_keeps_raw_provider_evidence():
    body = _two_turn_body()
    names = build_request_tool_name_map("openai", body)
    alias = names.provider_name("web.search")
    payload = {
        "id": "chatcmpl-1",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": alias,
                                "arguments": "{\"query\":\"latest\"}",
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }

    result = await OpenAIResponseChats().adapt_chat(
        _response(payload=payload),
        tool_names=names,
    )

    assert result.choices[0].message.tool_calls[0].function.name == "web.search"
    raw_call = (
        result.metadata.raw_response["choices"][0]["message"]["tool_calls"][0]
    )
    assert raw_call["function"]["name"] == alias


@pytest.mark.asyncio
async def test_openai_api_keeps_one_alias_map_across_outbound_and_response_decode():
    class Mapper:
        def translate(self, model):
            return model

    class Provider:
        mapper = Mapper()
        sent = None

        async def send(self, **kwargs):
            self.sent = kwargs["json"]
            alias = self.sent["tools"][0]["function"]["name"]
            return _response(
                payload={
                    "id": "chatcmpl-2",
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-provider",
                                        "type": "function",
                                        "function": {
                                            "name": alias,
                                            "arguments": "{\"query\":\"latest\"}",
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            )

    provider = Provider()
    result = await OpenAIChats(provider).chat(
        body={
            "model": "test-model",
            "messages": [{"role": "user", "content": "search"}],
            "tools": [_tool()],
        },
        http_client=object(),
        timeout=3,
    )

    alias = provider.sent["tools"][0]["function"]["name"]
    assert alias != "web.search"
    assert result.choices[0].message.tool_calls[0].function.name == "web.search"


def test_gemini_alias_is_identical_across_declaration_call_and_response_history():
    body = _two_turn_body()
    original = deepcopy(body)

    prepared = GeminiRequestChats().adapt_chat(body)

    alias = prepared["tools"][0]["function_declarations"][0]["name"]
    assert alias != "web.search"
    assert "." not in alias
    assert prepared["contents"][1]["parts"][0]["functionCall"]["name"] == alias
    assert prepared["contents"][2]["parts"][0]["functionResponse"]["name"] == alias
    assert body == original


@pytest.mark.asyncio
async def test_gemini_response_and_stream_restore_dotted_logical_name():
    body = {
        "tools": [_tool()],
        "messages": [{"role": "user", "content": "search"}],
    }
    names = build_request_tool_name_map("gemini", body)
    alias = names.provider_name("web.search")
    payload = {
        "modelVersion": "gemini-test",
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "id": "call-1",
                                "name": alias,
                                "args": {"query": "latest"},
                            }
                        }
                    ],
                },
                "finishReason": "STOP",
            }
        ],
    }

    result = await GeminiResponseChats().adapt_chat(
        _response(payload=payload),
        tool_names=names,
    )
    assert result.choices[0].message.tool_calls[0].function.name == "web.search"

    stream_response = _response(content=json.dumps(payload).encode("utf-8"))
    chunks = [
        chunk
        async for chunk in GeminiResponseChats().adapt_chat_stream(
            stream_response,
            tool_names=names,
        )
    ]
    assert chunks[0].choices[0].delta.tool_calls[0].function.name == "web.search"


@pytest.mark.asyncio
async def test_gemini_unknown_provider_tool_name_fails_closed():
    body = {
        "tools": [_tool()],
        "messages": [{"role": "user", "content": "search"}],
    }
    names = build_request_tool_name_map("gemini", body)
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "unknown_provider_name",
                                "args": {},
                            }
                        }
                    ]
                }
            }
        ]
    }

    with pytest.raises(ResponseValidationError, match="unknown provider tool name"):
        await GeminiResponseChats().adapt_chat(
            _response(payload=payload),
            tool_names=names,
        )


def test_gemini_converter_has_no_cross_request_alias_state():
    converter = GeminiRequestChats()
    first = converter.adapt_chat(
        {"messages": [], "tools": [_tool("web.search")]}
    )
    second = converter.adapt_chat(
        {"messages": [], "tools": [_tool("files.read")]}
    )

    first_name = first["tools"][0]["function_declarations"][0]["name"]
    second_name = second["tools"][0]["function_declarations"][0]["name"]
    assert first_name != second_name
    assert first_name.startswith("web_search_")
    assert second_name.startswith("files_read_")


def test_ollama_lowering_uses_native_tools_object_arguments_and_tool_name():
    body = _two_turn_body()
    original = deepcopy(body)
    names = build_request_tool_name_map("ollama", body)

    prepared = OllamaRequestChats().adapt_chat_request(
        body,
        tool_names=names,
    )

    assert prepared["tools"][0] == {
        "type": "function",
        "function": {
            "name": "web.search",
            "description": "Search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
    function = prepared["messages"][1]["tool_calls"][0]["function"]
    assert function["name"] == "web.search"
    assert function["arguments"] == {"query": "latest"}
    assert prepared["messages"][2] == {
        "role": "tool",
        "tool_name": "web.search",
        "content": '{"items": [1]}',
    }
    assert body == original


def test_ollama_invalid_string_arguments_fail_closed():
    body = {
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "web.search",
                            "arguments": "{not-json",
                        },
                    }
                ],
            }
        ]
    }

    with pytest.raises(ProviderToolContractError, match="valid JSON"):
        OllamaRequestChats().adapt_chat_request(body)


@pytest.mark.asyncio
async def test_ollama_response_normalizes_arguments_and_logical_name():
    body = {
        "tools": [_tool()],
        "messages": [{"role": "user", "content": "search"}],
    }
    names = build_request_tool_name_map("ollama", body)
    result = await OllamaResponseChats().adapt_chat(
        _response(
            payload={
                "model": "qwen3",
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "web.search",
                                "arguments": {"query": "latest"},
                            },
                        }
                    ],
                },
            }
        ),
        tool_names=names,
    )

    call = result.choices[0].message.tool_calls[0]
    assert call.function.name == "web.search"
    assert json.loads(call.function.arguments) == {"query": "latest"}


@pytest.mark.asyncio
async def test_ollama_api_two_turn_request_is_native_and_response_is_canonical():
    class Mapper:
        def translate(self, model):
            return model

    class Provider:
        mapper = Mapper()
        sent = None

        async def send(self, **kwargs):
            self.sent = kwargs["json"]
            return _response(
                payload={
                    "model": "qwen3",
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "web.search",
                                    "arguments": {"query": "latest"},
                                },
                            }
                        ],
                    },
                }
            )

    provider = Provider()
    result = await OllamaChats(provider).chat(
        body=_two_turn_body(),
        http_client=object(),
        timeout=3,
    )

    assert provider.sent["tools"][0]["type"] == "function"
    assert isinstance(
        provider.sent["messages"][1]["tool_calls"][0]["function"]["arguments"],
        dict,
    )
    assert provider.sent["messages"][2]["tool_name"] == "web.search"
    assert result.choices[0].message.tool_calls[0].function.name == "web.search"
