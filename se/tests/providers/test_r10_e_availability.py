from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from se.src.provider.exceptions import (
    PROVIDER_MODEL_UNAVAILABLE,
    PROVIDER_RESPONSE_INVALID,
    NoAvailableProviderError,
    ProviderModelUnavailableError,
    ResponseValidationError,
)
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
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


class _RuntimeProvider:
    def __init__(self, name: str, *, probe_error: Exception | None = None):
        self.name = name
        self.probe_error = probe_error
        self.probe_calls = 0

    async def has_capability(
        self,
        model,
        capability,
        http_client,
        timeout,
    ):
        self.probe_calls += 1
        if self.probe_error is not None:
            raise self.probe_error
        return True


class _RuntimeExecutor:
    def __init__(self):
        self.retry_policy = SimpleNamespace(max_retries=2)
        self.provider_calls = []
        self.budgets = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, call_budget, **kwargs):
        self.provider_calls.append(provider.name)
        self.budgets.append(call_budget)
        return "fallback-ok"


def _runtime_handler(providers, *, enable_fallback: bool):
    config = SimpleNamespace(
        priority=[provider.name for provider in providers],
        routing_rules_path="__r10_e_missing_rules__.yaml",
        enable_fallback=enable_fallback,
    )
    routing = RoutingPolicy(
        providers={provider.name: provider for provider in providers},
        config=config,
    )
    executor = _RuntimeExecutor()
    handler = ChatExecutionHandler(
        providers={provider.name: provider for provider in providers},
        routing_policy=routing,
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        timeout=10.0,
    )
    return handler, executor


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
@pytest.mark.parametrize(
    "payload",
    [
        {"error": "model not found"},
        {},
        [],
        {"unexpected": "shape"},
    ],
)
async def test_r10_e_ollama_success_status_malformed_shape_is_response_invalid(
    payload,
):
    response = SimpleNamespace(
        status_code=200,
        json=lambda: payload,
    )
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(return_value=response),
    )

    with pytest.raises(ResponseValidationError) as raised:
        await OllamaModels(provider).model(
            "logical-model",
            http_client=object(),
            timeout=1.0,
        )

    error = raised.value
    assert error.code == PROVIDER_RESPONSE_INVALID
    assert error.provider_name == "ollama"
    assert not isinstance(error, ProviderModelUnavailableError)
    assert error.raw_response == payload


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


@pytest.mark.asyncio
async def test_r10_e_model_unavailable_probe_skips_without_retry_charge():
    missing = ProviderModelUnavailableError(
        "mapped model missing",
        provider_name="p1",
        status_code=404,
    )
    p1 = _RuntimeProvider("p1", probe_error=missing)
    p2 = _RuntimeProvider("p2")
    handler, executor = _runtime_handler(
        [p1, p2],
        enable_fallback=True,
    )

    result = await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "fallback-ok"
    assert p1.probe_calls == 1
    assert p2.probe_calls == 1
    assert executor.provider_calls == ["p2"]
    assert len(executor.budgets) == 1
    assert executor.budgets[0].retries_used == 0


@pytest.mark.asyncio
async def test_r10_e_model_unavailable_does_not_bypass_fallback_disabled():
    missing = ProviderModelUnavailableError(
        "mapped model missing",
        provider_name="p1",
        status_code=404,
    )
    p1 = _RuntimeProvider("p1", probe_error=missing)
    p2 = _RuntimeProvider("p2")
    handler, executor = _runtime_handler(
        [p1, p2],
        enable_fallback=False,
    )

    with pytest.raises(NoAvailableProviderError):
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
        )

    assert p1.probe_calls == 1
    assert p2.probe_calls == 0
    assert executor.provider_calls == []


@pytest.mark.asyncio
async def test_r10_e_ollama_malformed_json_is_provider_response_failure():
    response = SimpleNamespace(
        status_code=200,
        text="{broken-json",
        json=lambda: (_ for _ in ()).throw(ValueError("invalid json")),
    )
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(return_value=response),
    )

    with pytest.raises(ResponseValidationError) as raised:
        await OllamaModels(provider).model(
            "logical-model",
            http_client=object(),
            timeout=1.0,
        )

    error = raised.value
    assert error.code == PROVIDER_RESPONSE_INVALID
    assert error.provider_name == "ollama"
    assert not isinstance(error, ProviderModelUnavailableError)
    assert isinstance(error.__cause__, ValueError)


@pytest.mark.asyncio
async def test_r10_e_malformed_provider_failure_remains_fallback_eligible():
    malformed = ResponseValidationError(
        "invalid provider response",
        provider_name="p1",
    )
    p1 = _RuntimeProvider("p1", probe_error=malformed)
    p2 = _RuntimeProvider("p2")
    handler, executor = _runtime_handler(
        [p1, p2],
        enable_fallback=True,
    )

    result = await handler.execute_with_fallback(
        object(),
        {"model": "logical-model"},
    )

    assert result == "fallback-ok"
    assert executor.provider_calls == ["p2"]
    assert executor.budgets[0].retries_used == 0


@pytest.mark.asyncio
async def test_r10_e_ollama_non_404_http_failure_is_not_model_unavailable():
    request = httpx.Request("POST", "http://ollama.test/api/show")
    response = httpx.Response(
        503,
        request=request,
        json={"error": "service unavailable"},
    )
    raw_error = httpx.HTTPStatusError(
        "service unavailable",
        request=request,
        response=response,
    )
    provider = SimpleNamespace(
        name="ollama",
        send=AsyncMock(side_effect=raw_error),
    )

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await OllamaModels(provider).model(
            "logical-model",
            http_client=object(),
            timeout=1.0,
        )

    assert raised.value is raw_error
