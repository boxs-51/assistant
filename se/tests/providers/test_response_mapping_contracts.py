import httpx
import pytest

from se.src.domain.schemas import GatewayResponse
from se.src.provider.exceptions import (
    ProviderUnavailableError,
    ResponseValidationError,
    wrap_provider_exception,
)
from se.src.provider.gemini.converters.chats.response import (
    ResponseChats as GeminiResponseChats,
)
from se.src.provider.gemini.converters.files.response import ResponseFiles
from se.src.provider.ollama.converters.chat.response import (
    ResponseChats as OllamaResponseChats,
)
from se.src.provider.openai.converters.chats.response import (
    ResponseChats as OpenAIResponseChats,
)


def _response(*, content: bytes = b"", json=None) -> httpx.Response:
    request = httpx.Request("POST", "https://provider.example/v1/chat")
    if json is not None:
        return httpx.Response(200, json=json, request=request)
    return httpx.Response(200, content=content, request=request)


def test_gateway_response_default_metadata_is_constructible():
    response = GatewayResponse(model="test-model")

    assert response.metadata.provider == "unknown"
    assert response.choices == []


@pytest.mark.asyncio
async def test_openai_invalid_json_is_mapped_to_response_validation_error():
    with pytest.raises(ResponseValidationError, match="OpenAI-compatible"):
        await OpenAIResponseChats().adapt_chat(_response(content=b"not-json"))


@pytest.mark.asyncio
async def test_openai_invalid_stream_chunk_is_not_silently_dropped():
    response = _response(content=b"data: not-json\n\n")

    with pytest.raises(ResponseValidationError, match="stream chunk"):
        async for _ in OpenAIResponseChats().adapt_chat_stream(response):
            pass


@pytest.mark.asyncio
async def test_ollama_invalid_message_shape_is_mapped_to_validation_error():
    response = _response(json={"model": "llama", "message": {"content": 42}})

    with pytest.raises(ResponseValidationError, match="Ollama"):
        await OllamaResponseChats().adapt_chat(response)


@pytest.mark.asyncio
async def test_gemini_invalid_candidate_shape_is_mapped_to_validation_error():
    response = _response(json={"candidates": [None]})

    with pytest.raises(ResponseValidationError, match="Gemini"):
        await GeminiResponseChats().adapt_chat(response)


@pytest.mark.asyncio
async def test_gemini_file_list_maps_decoded_entries_to_dto():
    response = _response(
        json={
            "files": [
                {
                    "name": "files/abc",
                    "displayName": "hello.txt",
                    "mimeType": "text/plain",
                    "sizeBytes": "5",
                    "uri": "https://provider.example/files/abc",
                }
            ]
        }
    )

    result = await ResponseFiles().adapt_file_list_response(response)

    assert len(result) == 1
    assert result[0].id == "abc"
    assert result[0].source == "provider"
    assert result[0].provider_file_id == "files/abc"


def test_all_server_errors_are_retryable_provider_unavailable_errors():
    request = httpx.Request("GET", "https://provider.example")
    response = httpx.Response(500, json={"error": {"message": "down"}}, request=request)

    error = wrap_provider_exception(
        httpx.HTTPStatusError("failed", request=request, response=response),
        "test-provider",
    )

    assert isinstance(error, ProviderUnavailableError)
    assert error.status_code == 500


def test_connection_errors_are_retryable_provider_unavailable_errors():
    request = httpx.Request("GET", "https://provider.example")
    error = wrap_provider_exception(httpx.ConnectError("offline", request=request), "test-provider")

    assert isinstance(error, ProviderUnavailableError)
    assert error.is_network_error is True
