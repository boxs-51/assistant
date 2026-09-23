from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from se.src.provider.exceptions import (
    PROVIDER_MODEL_UNAVAILABLE,
    ProviderModelUnavailableError,
)
from se.src.provider.ollama.api.models import OllamaModels
from se.src.provider.policies.routing_policy import RoutingPolicy


def _provider(name: str):
    return SimpleNamespace(name=name)


def _policy(*, enable_fallback: bool) -> RoutingPolicy:
    providers = {
        "p1": _provider("p1"),
        "p2": _provider("p2"),
    }
    config = SimpleNamespace(
        priority=["p1", "p2"],
        routing_rules_path="__r10_e_missing_rules__.yaml",
        enable_fallback=enable_fallback,
    )
    return RoutingPolicy(providers=providers, config=config)


def test_r10_e_enable_fallback_false_uses_only_first_resolved_provider():
    policy = _policy(enable_fallback=False)

    chain = policy.get_fallback_chain("logical-model")

    assert [provider.name for provider in chain] == ["p1"]


def test_r10_e_enable_fallback_false_keeps_preferred_provider_single():
    policy = _policy(enable_fallback=False)

    chain = policy.get_fallback_chain(
        "logical-model",
        metadata={
            "routing": {
                "prefer_provider": "p2",
                "type": "fallback",
            }
        },
    )

    assert [provider.name for provider in chain] == ["p2"]


def test_r10_e_strict_direct_remains_single_provider_when_fallback_enabled():
    policy = _policy(enable_fallback=True)

    chain = policy.get_fallback_chain(
        "logical-model",
        metadata={
            "routing": {
                "prefer_provider": "p2",
                "type": "strict",
            }
        },
    )

    assert [provider.name for provider in chain] == ["p2"]


def test_r10_e_strict_without_preference_uses_only_first_resolved_provider():
    policy = _policy(enable_fallback=True)

    chain = policy.get_fallback_chain(
        "logical-model",
        metadata={"routing": {"type": "strict"}},
    )

    assert [provider.name for provider in chain] == ["p1"]


@pytest.mark.asyncio
async def test_r10_e_ollama_404_is_stable_model_unavailable():
    request = httpx.Request("POST", "http://ollama.test/api/show")
    response = httpx.Response(
        404,
        request=request,
        json={"error": "model 'missing' not found"},
    )
    raw_error = httpx.HTTPStatusError(
        "model missing",
        request=request,
        response=response,
    )
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(side_effect=raw_error),
    )

    with pytest.raises(ProviderModelUnavailableError) as raised:
        await OllamaModels(provider).model(
            "missing",
            http_client=object(),
            timeout=1.0,
        )

    error = raised.value
    assert error.code == PROVIDER_MODEL_UNAVAILABLE
    assert error.provider_name == "ollama"
    assert error.status_code == 404
    assert error.__cause__ is raw_error


@pytest.mark.asyncio
async def test_r10_e_ollama_transport_failure_is_not_rewritten_as_model_missing():
    request = httpx.Request("POST", "http://ollama.test/api/show")
    raw_error = httpx.ConnectError("ollama unavailable", request=request)
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(side_effect=raw_error),
    )

    with pytest.raises(httpx.ConnectError) as raised:
        await OllamaModels(provider).model(
            "logical-model",
            http_client=object(),
            timeout=1.0,
        )

    assert raised.value is raw_error


@pytest.mark.asyncio
async def test_r10_e_ollama_error_payload_is_model_unavailable():
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"error": "model not found"},
    )
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(return_value=response),
    )

    with pytest.raises(ProviderModelUnavailableError) as raised:
        await OllamaModels(provider).model(
            "missing",
            http_client=object(),
            timeout=1.0,
        )

    assert raised.value.code == PROVIDER_MODEL_UNAVAILABLE
    assert raised.value.provider_name == "ollama"


@pytest.mark.asyncio
async def test_r10_e_ollama_model_list_skips_definitively_removed_model():
    list_response = SimpleNamespace(
        json=lambda: {"models": [{"name": "removed-model"}]},
    )
    request = httpx.Request("POST", "http://ollama.test/api/show")
    missing_response = httpx.Response(
        404,
        request=request,
        json={"error": "model not found"},
    )
    missing_error = httpx.HTTPStatusError(
        "model missing",
        request=request,
        response=missing_response,
    )
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(side_effect=[list_response, missing_error]),
    )

    result = await OllamaModels(provider).models(
        http_client=object(),
        timeout=1.0,
    )

    assert result.data == []
