from __future__ import annotations

import json

import httpx
import pytest

from se.src.application.user_inference_quota import (
    InferenceQuotaSettings,
    UserInferenceQuotaService,
)
from se.src.provider.gemini.converters.chats.response import (
    ResponseChats as GeminiResponseChats,
)
from se.src.provider.ollama.converters.chat.response import (
    ResponseChats as OllamaResponseChats,
)


def _response(*, payload=None, content: bytes | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://provider.example/v1/chat")
    if payload is not None:
        return httpx.Response(200, json=payload, request=request)
    return httpx.Response(200, content=content or b"", request=request)


def _normalizer() -> UserInferenceQuotaService:
    return UserInferenceQuotaService(
        lambda: None,
        owner_authority=object(),
        settings=InferenceQuotaSettings(),
    )


@pytest.mark.asyncio
async def test_gemini_nonstream_missing_usage_is_unknown():
    result = await GeminiResponseChats().adapt_chat(
        _response(payload={"candidates": [], "modelVersion": "gemini-test"})
    )

    assert result.usage.model_fields_set == set()
    usage = _normalizer().normalize_gateway_usage(result)
    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None


@pytest.mark.asyncio
async def test_gemini_nonstream_partial_usage_preserves_presence():
    result = await GeminiResponseChats().adapt_chat(
        _response(
            payload={
                "candidates": [],
                "modelVersion": "gemini-test",
                "usageMetadata": {"promptTokenCount": 7},
            }
        )
    )

    assert result.usage.model_fields_set == {"prompt_tokens"}
    usage = _normalizer().normalize_gateway_usage(result)
    assert usage.input_tokens == 7
    assert usage.output_tokens is None
    assert usage.total_tokens is None


@pytest.mark.asyncio
async def test_gemini_stream_explicit_zero_is_known_but_missing_fields_are_unknown():
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": "done"}]},
                "finishReason": "STOP",
            }
        ],
        "modelVersion": "gemini-test",
        "usageMetadata": {"promptTokenCount": 0},
    }
    response = _response(content=json.dumps(payload).encode("utf-8"))

    chunks = [
        chunk
        async for chunk in GeminiResponseChats().adapt_chat_stream(response)
    ]
    assert len(chunks) == 1
    assert chunks[0].usage is not None
    assert chunks[0].usage.model_fields_set == {"prompt_tokens"}

    usage = _normalizer().normalize_stream_usage(
        usage=chunks[0].usage,
        provider="gemini",
        model=chunks[0].model,
    )
    assert usage.input_tokens == 0
    assert usage.output_tokens is None
    assert usage.total_tokens is None


@pytest.mark.asyncio
async def test_gemini_complete_usage_derives_total_only_from_both_components():
    result = await GeminiResponseChats().adapt_chat(
        _response(
            payload={
                "candidates": [],
                "modelVersion": "gemini-test",
                "usageMetadata": {
                    "promptTokenCount": 2,
                    "candidatesTokenCount": 3,
                },
            }
        )
    )

    assert result.usage.model_fields_set == {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
    usage = _normalizer().normalize_gateway_usage(result)
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        2,
        3,
        5,
    )


@pytest.mark.asyncio
async def test_ollama_nonstream_partial_usage_preserves_presence():
    result = await OllamaResponseChats().adapt_chat(
        _response(
            payload={
                "model": "ollama-test",
                "done": True,
                "message": {"role": "assistant", "content": "done"},
                "prompt_eval_count": 4,
            }
        )
    )

    assert result.usage.model_fields_set == {"prompt_tokens"}
    usage = _normalizer().normalize_gateway_usage(result)
    assert usage.input_tokens == 4
    assert usage.output_tokens is None
    assert usage.total_tokens is None


@pytest.mark.asyncio
async def test_ollama_stream_partial_terminal_usage_preserves_unknown_dimension():
    payload = {
        "model": "ollama-test",
        "done": True,
        "message": {"role": "assistant", "content": ""},
        "eval_count": 3,
    }
    response = _response(
        content=(json.dumps(payload) + "\n").encode("utf-8")
    )

    chunks = [
        chunk
        async for chunk in OllamaResponseChats().adapt_chat_stream(response)
    ]
    assert len(chunks) == 1
    assert chunks[0].usage is not None
    assert chunks[0].usage.model_fields_set == {"completion_tokens"}

    usage = _normalizer().normalize_stream_usage(
        usage=chunks[0].usage,
        provider="ollama",
        model=chunks[0].model,
    )
    assert usage.input_tokens is None
    assert usage.output_tokens == 3
    assert usage.total_tokens is None


@pytest.mark.asyncio
async def test_ollama_complete_usage_remains_known():
    result = await OllamaResponseChats().adapt_chat(
        _response(
            payload={
                "model": "ollama-test",
                "done": True,
                "message": {"role": "assistant", "content": "done"},
                "prompt_eval_count": 2,
                "eval_count": 3,
            }
        )
    )

    usage = _normalizer().normalize_gateway_usage(result)
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
        2,
        3,
        5,
    )
