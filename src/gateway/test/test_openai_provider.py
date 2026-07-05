import pytest
import httpx
import json
import time
from typing import AsyncGenerator

from gateway.routing.providers.openai import OpenAIProvider
from gateway.schemas import GatewayResponse, GatewayStreamChunk

pytestmark = pytest.mark.asyncio


@pytest.fixture
def provider():
    """Fixture để cung cấp một instance của OpenAIProvider."""
    return OpenAIProvider()


async def test_normalize_response_success(provider: OpenAIProvider):
    """
    Kiểm tra xem normalize_response có ánh xạ đúng response của OpenAI
    vào GatewayResponse hay không.
    """
    # 1. Dữ liệu giả lập từ OpenAI
    openai_payload = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-4o",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello! I am a language model.",
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30
        }
    }
    mock_response = httpx.Response(
        200,
        json=openai_payload,
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )

    # 2. Gọi phương thức cần test
    gateway_response = await provider.normalize_response(mock_response)

    # 3. Kiểm tra kết quả
    assert isinstance(gateway_response, GatewayResponse)
    assert gateway_response.model == "gpt-4o"
    assert gateway_response.choices[0].message.content == "Hello! I am a language model."
    assert gateway_response.usage.prompt_tokens == 10
    assert gateway_response.usage.completion_tokens == 20
    assert gateway_response.raw_response == mock_response


async def test_normalize_stream_success(provider: OpenAIProvider):
    """
    Kiểm tra xem normalize_stream có ánh xạ đúng stream của OpenAI
    vào một AsyncGenerator các GatewayStreamChunk hay không.
    """
    # 1. Dữ liệu stream giả lập từ OpenAI
    stream_chunks_raw = [
        {"id": "chatcmpl-123", "model": "gpt-4o", "choices": [{"index": 0, "delta": {"content": "Hello"}}]},
        {"id": "chatcmpl-123", "model": "gpt-4o", "choices": [{"index": 0, "delta": {"content": "!"}}]},
        {"id": "chatcmpl-123", "model": "gpt-4o", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]

    async def mock_stream() -> AsyncGenerator[bytes, None]:
        for chunk in stream_chunks_raw:
            yield f"data: {json.dumps(chunk)}\n\n".encode('utf-8')
        yield b"data: [DONE]\n\n"

    mock_response = httpx.Response(
        200,
        content=mock_stream(),
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )
    mock_response.aiter_bytes = mock_stream

    # 2. Gọi phương thức và thu thập kết quả
    results = [chunk async for chunk in provider.normalize_stream(mock_response)]

    # 3. Kiểm tra kết quả
    assert len(results) == 3
    assert isinstance(results[0], GatewayStreamChunk)
    assert results[0].choices[0].delta.content == "Hello"
    assert results[2].choices[0].finish_reason == "stop"

# =================================================================
# KÍCH HOẠT CHẠY TRỰC TIẾP
# =================================================================
if __name__ == "__main__":
    import sys
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    sys.exit(pytest.main(["-v", "-s", __file__]))