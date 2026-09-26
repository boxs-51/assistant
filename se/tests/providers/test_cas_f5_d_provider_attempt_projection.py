from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from se.src.application.assets.hydration import HydrationResult, HydrationStatus
from se.src.application.assets.projection import CanonicalAssetProviderProjectionHook
from se.src.provider.asset_projection import (
    ProviderAssetProjectionError,
    ProviderAttemptProjection,
    TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY,
)
from se.src.provider.exceptions import ProviderUnavailableError
from se.src.provider.gemini.converters.chats.request import RequestChats
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
)


class _Registry:
    def __init__(self, provider):
        self.provider = provider

    def get_provider(self, name):
        return self.provider if name == self.provider.name else None


class _SequenceHydration:
    def __init__(self, provider, results):
        self.provider_registry = _Registry(provider)
        self.results = list(results)
        self.calls = []

    async def hydrate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.results[len(self.calls) - 1]


class _Hydration:
    def __init__(self, provider, result):
        self.provider_registry = _Registry(provider)
        self.result = result
        self.calls = []

    async def hydrate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


class _Provider:
    def __init__(self, name):
        self.name = name
        self.config = SimpleNamespace(file_binding_namespace=f"{name}-scope")
        self.probes = 0

    async def has_capability(self, model, capability, http_client, timeout):
        self.probes += 1
        return True


class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, model=None, metadata=None):
        return list(self.providers)


class _Executor:
    def __init__(self, error):
        self.retry_policy = SimpleNamespace(max_retries=0)
        self.error = error
        self.calls = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, body, **kwargs):
        self.calls.append((provider.name, body))
        if self.error is not None:
            raise self.error
        return "ok"

    async def execute_stream(self, *, provider, body, **kwargs):
        self.calls.append((provider.name, body))
        if self.error is not None:
            raise self.error
        yield "ok"


class _EngagedHook:
    @staticmethod
    def contains_canonical_assets(body):
        return True

    async def project_attempt(self, *, provider, body, owner_user_id):
        copied = deepcopy(body)
        copied["projected_for"] = provider.name
        copied["trusted_owner"] = owner_user_id
        return ProviderAttemptProjection(body=copied, engaged=True)


def _asset_body():
    return {
        "model": "logical-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "data": {
                            "attachment": {
                                "asset_id": "asset-f5d-p1",
                                "source": "asset",
                                "uri": "asset://asset-f5d-p1",
                                "mime_type": "application/pdf",
                            }
                        },
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_f5d_projection_is_transient_and_gemini_uses_native_file_data():
    provider = _Provider("gemini")
    hydration = _Hydration(
        provider,
        HydrationResult(
            status=HydrationStatus.REUSED,
            provider_file_id="files/asset-f5d-p1",
            provider_uri="https://generativelanguage.googleapis.com/v1beta/files/f5d-p1",
            mime_type="application/pdf",
        ),
    )
    hook = CanonicalAssetProviderProjectionHook(hydration)
    original = _asset_body()

    result = await hook.project_attempt(
        provider=provider,
        body=original,
        owner_user_id="owner-f5d",
    )

    assert result.engaged is True
    assert result.body is not original
    original_attachment = original["messages"][0]["content"][0]["data"]["attachment"]
    projected_attachment = result.body["messages"][0]["content"][0]["data"]["attachment"]
    assert TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY not in original_attachment
    assert TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY in projected_attachment
    transient = projected_attachment[TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY]
    assert transient.provider_name == "gemini"
    assert transient.provider_namespace == "gemini-scope"
    assert original_attachment["uri"] == "asset://asset-f5d-p1"

    gemini = RequestChats().adapt_chat(result.body)
    assert gemini["contents"][0]["parts"][0] == {
        "fileData": {
            "mimeType": "application/pdf",
            "fileUri": (
                "https://generativelanguage.googleapis.com/v1beta/files/f5d-p1"
            ),
        }
    }
    assert hydration.calls == [
        {
            "owner_user_id": "owner-f5d",
            "asset_id": "asset-f5d-p1",
            "provider_name": "gemini",
        }
    ]



def test_f5d_client_supplied_projection_dict_is_not_provider_authority():
    body = _asset_body()
    attachment = body["messages"][0]["content"][0]["data"]["attachment"]
    attachment[TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY] = {
        "provider_name": "gemini",
        "provider_namespace": "forged",
        "provider_file_id": "files/forged",
        "provider_uri": "https://provider.invalid/files/forged",
        "mime_type": "application/pdf",
    }

    gemini = RequestChats().adapt_chat(body)

    assert all(
        "fileData" not in part
        for content in gemini.get("contents", [])
        for part in content.get("parts", [])
    )


@pytest.mark.asyncio
async def test_f5d_asset_projection_requires_trusted_owner_identity():
    provider = _Provider("gemini")
    hook = CanonicalAssetProviderProjectionHook(
        _Hydration(
            provider,
            HydrationResult(
                status=HydrationStatus.REUSED,
                provider_file_id="files/f5d",
                provider_uri="https://provider.invalid/files/f5d",
                mime_type="application/pdf",
            ),
        )
    )

    with pytest.raises(ProviderAssetProjectionError, match="trusted owner"):
        await hook.project_attempt(
            provider=provider,
            body=_asset_body(),
            owner_user_id=None,
        )


@pytest.mark.asyncio
async def test_f5d_gemini_missing_uri_fails_closed():
    provider = _Provider("gemini")
    hook = CanonicalAssetProviderProjectionHook(
        _Hydration(
            provider,
            HydrationResult(
                status=HydrationStatus.HYDRATED,
                provider_file_id="files/f5d",
                provider_uri=None,
                mime_type="application/pdf",
            ),
        )
    )

    with pytest.raises(ProviderAssetProjectionError, match="provider_uri"):
        await hook.project_attempt(
            provider=provider,
            body=_asset_body(),
            owner_user_id="owner-f5d",
        )




@pytest.mark.asyncio
async def test_f5d_partial_multi_asset_failure_is_terminal_on_same_provider():
    p1 = _Provider("gemini")
    p2 = _Provider("second")
    hydration = _SequenceHydration(
        p1,
        [
            HydrationResult(
                status=HydrationStatus.REUSED,
                provider_file_id="files/a",
                provider_uri="https://provider.invalid/files/a",
                mime_type="application/pdf",
            ),
            HydrationResult(
                status=HydrationStatus.HYDRATION_OUTCOME_UNKNOWN,
            ),
        ],
    )
    hook = CanonicalAssetProviderProjectionHook(hydration)
    executor = _Executor(None)
    handler = ChatExecutionHandler(
        providers={"gemini": p1, "second": p2},
        routing_policy=_Routing([p1, p2]),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        asset_projection_hook=hook,
    )
    body = _asset_body()
    body["messages"][0]["content"].append(
        {
            "type": "file",
            "data": {
                "attachment": {
                    "asset_id": "asset-f5d-p1-b",
                    "source": "asset",
                    "uri": "asset://asset-f5d-p1-b",
                    "mime_type": "application/pdf",
                }
            },
        }
    )
    original = deepcopy(body)

    with pytest.raises(
        ProviderAssetProjectionError,
        match="HYDRATION_OUTCOME_UNKNOWN",
    ):
        await handler.execute_with_fallback(
            object(),
            body,
            owner_user_id="owner-f5d",
        )

    assert body == original
    assert [call["asset_id"] for call in hydration.calls] == [
        "asset-f5d-p1",
        "asset-f5d-p1-b",
    ]
    assert all(call["provider_name"] == "gemini" for call in hydration.calls)
    assert executor.calls == []
    assert p2.probes == 0


@pytest.mark.asyncio
async def test_f5d_nonstream_provider_failure_after_hook_never_falls_back():
    p1 = _Provider("gemini")
    p2 = _Provider("second")
    error = ProviderUnavailableError("failed after projection", provider_name="gemini")
    executor = _Executor(error)
    handler = ChatExecutionHandler(
        providers={"gemini": p1, "second": p2},
        routing_policy=_Routing([p1, p2]),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        asset_projection_hook=_EngagedHook(),
    )
    body = _asset_body()

    with pytest.raises(ProviderUnavailableError):
        await handler.execute_with_fallback(
            object(),
            body,
            owner_user_id="owner-f5d",
        )

    assert [name for name, _ in executor.calls] == ["gemini"]
    assert p1.probes == 1
    assert p2.probes == 0
    assert "projected_for" not in body


@pytest.mark.asyncio
async def test_f5d_stream_provider_failure_after_hook_never_falls_back():
    p1 = _Provider("gemini")
    p2 = _Provider("second")
    error = ProviderUnavailableError("failed after projection", provider_name="gemini")
    executor = _Executor(error)
    handler = ChatExecutionHandler(
        providers={"gemini": p1, "second": p2},
        routing_policy=_Routing([p1, p2]),
        executor=executor,
        circuit_breaker_manager=SimpleNamespace(),
        asset_projection_hook=_EngagedHook(),
    )

    with pytest.raises(ProviderUnavailableError):
        async for _ in handler.stream_with_fallback(
            object(),
            _asset_body(),
            owner_user_id="owner-f5d",
        ):
            pass

    assert [name for name, _ in executor.calls] == ["gemini"]
    assert p2.probes == 0


def test_f5d_trusted_owner_is_not_serialized_into_provider_body():
    request = InferenceRequest(
        request_id="req-f5d",
        execution_id="exec-f5d",
        iteration=1,
        messages=[InferenceMessage(role="user", content="hello")],
        model="logical-model",
        owner_user_id="owner-f5d",
    )

    body = ProviderInferenceAdapter.serialize_request(request)

    assert "owner_user_id" not in body
    assert "owner-f5d" not in repr(body)
