import pytest
import httpx
import json
from typing import AsyncGenerator

from src.gateway.routing.providers.ollama.ollama import OllamaProvider
from src.gateway.schemas import GatewayResponse, GatewayStreamChunk

# Sử dụng pytest-asyncio để chạy các test bất đồng bộ
pytestmark = pytest.mark.asyncio


@pytest.fixture
def provider():
    """Fixture để cung cấp một instance của OllamaProvider."""
    return OllamaProvider()


async def test_normalize_response_success(provider: OllamaProvider):
    """
    Kiểm tra xem normalize_response có chuyển đổi đúng định dạng JSON của Ollama
    thành GatewayResponse hay không.
    """
    # 1. Dữ liệu giả lập từ Ollama
    ollama_payload = {
        "model": "llama3-8b",
        "created_at": "2023-08-04T08:52:19.323Z",
        "response": "Xin chào! Tôi là một mô hình ngôn ngữ.",
        "done": True,
        "total_duration": 5021584458,
        "load_duration": 21584458,
        "prompt_eval_count": 26,
        "eval_count": 29,
    }
    mock_response = httpx.Response(
        200,
        content=json.dumps(ollama_payload),
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
    )

    # 2. Gọi phương thức cần test
    gateway_response = await provider.normalize_response(mock_response)

    # 3. Kiểm tra kết quả
    assert isinstance(gateway_response, GatewayResponse)
    assert gateway_response.model == "llama3-8b"
    assert len(gateway_response.choices) == 1
    assert gateway_response.choices[0].message.role == "assistant"
    assert gateway_response.choices[0].message.content == "Xin chào! Tôi là một mô hình ngôn ngữ."
    assert gateway_response.choices[0].finish_reason == "stop"
    assert gateway_response.usage.prompt_tokens == 26
    assert gateway_response.usage.completion_tokens == 29
    assert gateway_response.usage.total_tokens == 55
    assert gateway_response.raw_response == mock_response


async def test_normalize_stream_success(provider: OllamaProvider):
    """
    Kiểm tra xem normalize_stream có chuyển đổi đúng một stream JSON của Ollama
    thành một AsyncGenerator các GatewayStreamChunk hay không.
    """
    # 1. Dữ liệu stream giả lập từ Ollama
    stream_chunks_raw = [
        {"model": "llama3-8b", "response": "Xin ", "done": False},
        {"model": "llama3-8b", "response": "chào", "done": False},
        {"model": "llama3-8b", "response": "!", "done": False},
        {"model": "llama3-8b", "response": "", "done": True, "prompt_eval_count": 10, "eval_count": 3},
    ]

    async def mock_stream() -> AsyncGenerator[bytes, None]:
        for chunk in stream_chunks_raw:
            yield json.dumps(chunk).encode('utf-8') + b'\n'

    mock_response = httpx.Response(
        200,
        content=mock_stream(),
        request=httpx.Request("POST", "http://localhost:11434/api/chat"),
    )
    # Đánh lừa httpx để nó nghĩ rằng đây là một stream
    mock_response.aiter_bytes = mock_stream

    # 2. Gọi phương thức cần test và thu thập kết quả
    results = []
    async for chunk in provider.normalize_stream(mock_response):
        results.append(chunk)

    # 3. Kiểm tra kết quả
    assert len(results) == 4  # 3 chunk nội dung + 1 chunk kết thúc

    # Kiểm tra chunk đầu tiên
    assert isinstance(results[0], GatewayStreamChunk)
    assert results[0].model == "llama3-8b"
    assert results[0].choices[0].delta.content == "Xin "
    assert results[0].choices[0].finish_reason is None

    # Kiểm tra chunk ở giữa
    assert results[1].choices[0].delta.content == "chào"

    # Kiểm tra chunk cuối cùng (chứa thông tin kết thúc)
    assert results[3].choices[0].delta.content is None
    assert results[3].choices[0].finish_reason == "stop"
    # Ollama stream không trả về usage trong chunk cuối, nên không cần test

# =================================================================
# KÍCH HOẠT CHẠY TRỰC TIẾP
# =================================================================
if __name__ == "__main__":
    import sys
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    sys.exit(pytest.main(["-v", "-s", __file__]))