from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from se.src.provider.ollama.api.models import OllamaModels


@pytest.mark.asyncio
async def test_ollama_model_list_parses_sync_httpx_json_response():
    response = SimpleNamespace(json=lambda: {"models": []})
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(return_value=response),
    )

    result = await OllamaModels(provider).models(
        http_client=SimpleNamespace(),
        timeout=1.0,
    )

    assert result.data == []
