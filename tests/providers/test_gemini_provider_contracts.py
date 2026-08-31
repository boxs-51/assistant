import httpx
import pytest

from src.infrastructure.config.schemas import ProviderConfig
from src.provider.core.api import ApiType
from src.provider.gemini import GeminiProvider
from src.provider.gemini.api.embeddings import GeminiEmbeddings
from src.provider.gemini.api.models import GeminiModels
from src.provider.gemini.converters.embeddings.request import RequestEmbeddings
from src.provider.gemini.converters.embeddings.response import ResponseEmbeddings


def _provider() -> GeminiProvider:
    return GeminiProvider(
        config=ProviderConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://generativelanguage.googleapis.com/",
        )
    )


def test_model_and_models_are_distinct_api_types():
    assert ApiType.MODEL is not ApiType.MODELS


def test_gemini_list_models_endpoint_does_not_require_model_argument():
    provider = _provider()
    assert provider.build_endpoint(ApiType.MODELS) == (
        "https://generativelanguage.googleapis.com/v1beta/models"
    )


def test_gemini_model_endpoint_requires_model_argument():
    provider = _provider()
    assert provider.build_endpoint(ApiType.MODEL, model="gemini-2.5-flash") == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash"
    )


def test_embedding_request_uses_requested_model_and_valid_batch_shape():
    body = RequestEmbeddings().adapt_embeddings_request({
        "model": "gemini-embedding-001",
        "input": ["hello"],
    })
    assert body == {
        "requests": [{
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": "hello"}]},
        }]
    }


def test_single_embedding_request_keeps_model_in_path_not_body():
    body = RequestEmbeddings().adapt_embeddings_request({
        "model": "gemini-embedding-001",
        "input": "hello",
    })
    assert body == {"content": {"parts": [{"text": "hello"}]}}


@pytest.mark.asyncio
async def test_embedding_response_is_normalized_to_gateway_shape():
    response = httpx.Response(
        200,
        json={"embeddings": [{"values": [0.1, 0.2]}]},
        request=httpx.Request("POST", "https://example.test"),
    )
    result = await ResponseEmbeddings().adapt_embeddings_response(
        response,
        "gemini-embedding-001",
    )
    assert result["data"][0]["embedding"] == [0.1, 0.2]