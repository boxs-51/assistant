from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from se.src.application.assets.hydration import HydrationResult, HydrationStatus
from se.src.application.assets.projection import (
    AssetProjectionError,
    ProviderPinnedAssetProjector,
)
from se.src.provider.core.asset_projection import ProviderAssetProjection
from se.src.provider.exceptions import ProviderError
from se.src.provider.gemini.converters.chats.request import RequestChats
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.runtimes.agent.adapters.inference import ProviderInferenceAdapter
from se.src.runtimes.agent.contracts.inference import (
    InferenceMessage,
    InferenceRequest,
)


def _asset_body() -> dict:
    return {
        "model": "gemini-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "data": {
                            "asset_id": "asset-f5d-p1",
                            "source": "asset",
                            "uri": "asset://asset-f5d-p1",
                            "mime_type": "application/pdf",
                        },
                    }
                ],
            }
        ],
    }


def _provider(name: str = "gemini"):
    return SimpleNamespace(
        name=name,
        config=SimpleNamespace(file_binding_namespace=f"{name}-tenant"),
    )


class _Hydration:
    def __init__(self, result: HydrationResult):
        self.result = result
        self.calls = []

    async def hydrate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.result


@pytest.mark.asyncio
async def test_f5d_projection_is_copy_only_and_gemini_uses_native_file_data():
    hydration = _Hydration(
        HydrationResult(
            status=HydrationStatus.REUSED,
            binding_id="binding-1",
            provider_file_id="files/provider-copy",
            provider_uri=(
                "https://generativelanguage.googleapis.com/"
                "v1beta/files/provider-copy"
            ),
            mime_type="application/pdf",
        )
    )
    projector = ProviderPinnedAssetProjector(hydration)
    source = _asset_body()

    projected = await projector.project(
        body=source,
        owner_user_id="owner-1",
        provider=_provider(),
    )

    original_part = source["messages"][0]["content"][0]
    projected_part = projected.body["messages"][0]["content"][0]
    assert "_provider_asset_projection" not in original_part
    assert projected.asset_count == 1

    authority = projected_part["_provider_asset_projection"]
    assert isinstance(authority, ProviderAssetProjection)
    assert authority.provider_name == "gemini"
    assert authority.provider_namespace == "gemini-tenant"
    assert authority.provider_file_id == "files/provider-copy"
    assert authority.mime_type == "application/pdf"
    assert hydration.calls == [
        {
            "owner_user_id": "owner-1",
            "asset_id": "asset-f5d-p1",
            "provider_name": "gemini",
        }
    ]

    gemini = RequestChats().adapt_chat(projected.body)
    assert gemini["contents"][0]["parts"] == [
        {
            "fileData": {
                "mimeType": "application/pdf",
                "fileUri": (
                    "https://generativelanguage.googleapis.com/"
                    "v1beta/files/provider-copy"
                ),
            }
        }
    ]


@pytest.mark.asyncio
async def test_f5d_gemini_missing_uri_fails_closed():
    projector = ProviderPinnedAssetProjector(
        _Hydration(
            HydrationResult(
                status=HydrationStatus.HYDRATED,
                binding_id="binding-1",
                provider_file_id="files/provider-copy",
                provider_uri=None,
                mime_type="application/pdf",
            )
        )
    )

    with pytest.raises(AssetProjectionError, match="non-blank provider_uri"):
        await projector.project(
            body=_asset_body(),
            owner_user_id="owner-1",
            provider=_provider(),
        )


@pytest.mark.asyncio
async def test_f5d_noneligible_hydration_status_fails_closed():
    projector = ProviderPinnedAssetProjector(
        _Hydration(
            HydrationResult(
                status=HydrationStatus.HYDRATION_OUTCOME_UNKNOWN,
                binding_id="binding-1",
            )
        )
    )

    with pytest.raises(AssetProjectionError, match="not projection-eligible"):
        await projector.project(
            body=_asset_body(),
            owner_user_id="owner-1",
            provider=_provider(),
        )


class _Routing:
    def __init__(self, providers):
        self.providers = providers

    def get_fallback_chain(self, **kwargs):
        return list(self.providers)


class _Provider:
    def __init__(self, name):
        self.name = name
        self.config = SimpleNamespace(file_binding_namespace=f"{name}-tenant")

    async def has_capability(self, *args, **kwargs):
        return True


class _Executor:
    def __init__(self):
        self.calls = []

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, **kwargs):
        self.calls.append(kwargs["provider"].name)
        raise ProviderError(
            "provider inference failed",
            provider_name=kwargs["provider"].name,
        )

    def execute_stream(self, **kwargs):
        self.calls.append(kwargs["provider"].name)

        async def _stream():
            raise ProviderError(
                "provider stream failed",
                provider_name=kwargs["provider"].name,
            )
            yield None

        return _stream()


class _FailingProjector:
    def __init__(self):
        self.calls = []

    async def project(self, *, body, owner_user_id, provider):
        self.calls.append(provider.name)
        raise AssetProjectionError(
            "projection failed",
            provider_name=provider.name,
        )


def _handler(projector, executor):
    providers = [_Provider("gemini"), _Provider("other")]
    return ChatExecutionHandler(
        providers={item.name: item for item in providers},
        routing_policy=_Routing(providers),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=10,
        asset_projector=projector,
        asset_projection_enabled=True,
    )


@pytest.mark.asyncio
async def test_f5d_hook_entry_is_terminal_before_projection_completes():
    projector = _FailingProjector()
    executor = _Executor()
    handler = _handler(projector, executor)

    with pytest.raises(AssetProjectionError, match="projection failed"):
        await handler.execute_with_fallback(
            object(),
            _asset_body(),
            owner_user_id="owner-1",
        )

    assert projector.calls == ["gemini"]
    assert executor.calls == []


@pytest.mark.asyncio
async def test_f5d_stream_hook_entry_is_terminal_before_projection_completes():
    projector = _FailingProjector()
    executor = _Executor()
    handler = _handler(projector, executor)

    with pytest.raises(AssetProjectionError, match="projection failed"):
        async for _ in handler.stream_with_fallback(
            object(),
            _asset_body(),
            owner_user_id="owner-1",
        ):
            pass

    assert projector.calls == ["gemini"]
    assert executor.calls == []


@pytest.mark.asyncio
async def test_f5d_adapter_hands_owner_separately_from_serialized_body():
    captured = {}

    class _Handler:
        async def execute_with_fallback(
            self,
            http_client,
            body,
            *,
            deadline_monotonic=None,
            owner_user_id=None,
        ):
            captured["body"] = body
            captured["owner_user_id"] = owner_user_id
            raise RuntimeError("stop-after-handoff")

    runtime = SimpleNamespace(chat_handler=_Handler())
    adapter = ProviderInferenceAdapter(runtime, object())
    request = InferenceRequest(
        request_id="req-f5d-owner",
        execution_id="exec-f5d-owner",
        iteration=1,
        messages=[InferenceMessage(role="user", content="hello")],
        model="mock",
        owner_user_id="trusted-owner",
    )

    with pytest.raises(RuntimeError, match="stop-after-handoff"):
        await adapter.complete(request)

    assert captured["owner_user_id"] == "trusted-owner"
    assert "owner_user_id" not in captured["body"]
    assert "owner_user_id" not in request.model_dump()


def test_f5d_direct_agent_identity_handoff_and_activation_remain_bounded():
    direct = Path("se/src/runtimes/chat/direct.py").read_text(encoding="utf-8")
    agent = Path("se/src/runtimes/agent/runtime.py").read_text(encoding="utf-8")
    provider_runtime = Path(
        "se/src/runtimes/provider/runtime.py"
    ).read_text(encoding="utf-8")
    workflow = Path(
        "se/src/runtimes/workflow/runtime.py"
    ).read_text(encoding="utf-8")

    assert "owner_user_id=identity.user_id" in direct
    assert "owner_user_id=context.identity.user_id" in agent
    assert "asset_projection_enabled=False" in provider_runtime
    assert "ASSET_HYDRATION_REQUIRED" in workflow
