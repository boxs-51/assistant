from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from se.src.application.assets.generated import (
    GeneratedAssetCanonicalizer,
    GeneratedMediaCanonicalizationError,
)
from se.src.domain.schemas import (
    FunctionCall,
    GatewayAttachment,
    GatewayChoice,
    GatewayMessage,
    GatewayResponse,
    GatewayToolCall,
    MessageContentPart,
    ResponseMetaData,
)
from se.src.domain.schemas.attachment import UrlContent
from se.src.provider.gemini.converters.chats.response import ResponseChats
from se.src.provider.handlers.chat_handler import ChatExecutionHandler
from se.src.runtimes.provider.runtime import ProviderRuntime


class _AssetService:
    def __init__(self):
        self.calls = []

    async def ingest_stream(self, **kwargs):
        payload = b"".join([chunk async for chunk in kwargs["stream"]])
        call = dict(kwargs)
        call["payload"] = payload
        call.pop("stream")
        self.calls.append(call)
        return SimpleNamespace(
            asset_id="asset-f7-p1",
            filename=kwargs["filename"],
            mime_type=kwargs["mime_type"],
            size_bytes=len(payload),
            sha256="a" * 64,
            state="READY",
            uri="asset://asset-f7-p1",
        )


def _attachment(payload: bytes = b"generated") -> GatewayAttachment:
    return GatewayAttachment(
        id="att-generated",
        filename="generated.bin",
        mime_type="application/octet-stream",
        base64_data=base64.b64encode(payload).decode("ascii"),
        source="base64",
    )


def _response(
    *,
    attachments_by_choice: list[list[GatewayAttachment]] | None = None,
    tool_calls: bool = False,
) -> GatewayResponse:
    attachments_by_choice = attachments_by_choice or [[]]
    choices = []
    for index, attachments in enumerate(attachments_by_choice):
        parts = [
            MessageContentPart(type="file", data=attachment)
            for attachment in attachments
        ]
        calls = None
        if index == 0 and tool_calls:
            calls = [
                GatewayToolCall(
                    id="call-f7",
                    function=FunctionCall(name="tool.read", arguments="{}"),
                )
            ]
        choices.append(
            GatewayChoice(
                index=index,
                message=GatewayMessage(
                    role="assistant",
                    content=parts or "text-only",
                    tool_calls=calls,
                ),
                finish_reason="tool_calls" if calls else "stop",
            )
        )
    return GatewayResponse(
        id="gateway-response-f7",
        model="model-f7",
        choices=choices,
        metadata=ResponseMetaData(
            provider="gemini",
            provider_response_id="provider-response-f7",
            raw_response={"transient": "must-not-escape"},
        ),
    )


@pytest.mark.asyncio
async def test_f7_p1_zero_media_passes_through_when_persistence_unavailable():
    response = _response()
    canonicalizer = GeneratedAssetCanonicalizer.unavailable()

    result = await canonicalizer.canonicalize(
        response,
        owner_user_id=None,
    )

    assert result is response
    assert result.metadata.raw_response == {"transient": "must-not-escape"}


@pytest.mark.asyncio
async def test_f7_p1_canonicalizes_one_terminal_inline_attachment_once():
    service = _AssetService()
    response = _response(attachments_by_choice=[[_attachment(b"abc")]])
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=1024,
    )

    result = await canonicalizer.canonicalize(
        response,
        owner_user_id="user-f7",
    )

    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["owner_user_id"] == "user-f7"
    assert call["payload"] == b"abc"
    assert call["content_length"] == 3
    assert call["max_bytes"] == 1024
    assert call["origin_type"] == "ASSISTANT"
    assert call["origin_id"] == "provider-response-f7"

    attachment = result.choices[0].message.content[0].data
    assert isinstance(attachment, GatewayAttachment)
    assert attachment.asset_id == "asset-f7-p1"
    assert attachment.source == "asset"
    assert attachment.uri == "asset://asset-f7-p1"
    assert attachment.base64_data is None
    assert attachment.provider_file_id is None
    assert result.metadata.raw_response is None

    # The provider response remains untouched; substitution happens on a copy.
    original = response.choices[0].message.content[0].data
    assert original.source == "base64"
    assert original.base64_data is not None


@pytest.mark.asyncio
async def test_f7_p1_rejects_tool_call_media_before_first_ingest():
    service = _AssetService()
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=1024,
    )

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await canonicalizer.canonicalize(
            _response(
                attachments_by_choice=[[_attachment()]],
                tool_calls=True,
            ),
            owner_user_id="user-f7",
        )

    assert exc.value.code == "CAS_F7_NONTERMINAL_TOOL_CALL_MEDIA"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_p1_rejects_multi_object_response_before_first_ingest():
    service = _AssetService()
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=1024,
    )

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await canonicalizer.canonicalize(
            _response(
                attachments_by_choice=[
                    [_attachment(b"a")],
                    [_attachment(b"b")],
                ]
            ),
            owner_user_id="user-f7",
        )

    assert exc.value.code == "CAS_F7_MULTI_OBJECT_UNSUPPORTED"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_p1_rejects_media_outside_selected_choice_before_ingest():
    service = _AssetService()
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=1024,
    )

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await canonicalizer.canonicalize(
            _response(attachments_by_choice=[[], [_attachment()]]),
            owner_user_id="user-f7",
        )

    assert exc.value.code == "CAS_F7_NON_SELECTED_CHOICE_MEDIA"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_p1_rejects_generated_media_when_asset_service_unavailable():
    response = _response(attachments_by_choice=[[_attachment()]])
    canonicalizer = GeneratedAssetCanonicalizer.unavailable()

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await canonicalizer.canonicalize(
            response,
            owner_user_id="user-f7",
        )

    assert exc.value.code == "CAS_F7_PERSISTENCE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_f7_p1_ordinary_url_content_passes_through_without_ingest():
    service = _AssetService()
    response = GatewayResponse(
        id="gateway-response-url",
        model="model-f7",
        choices=[
            GatewayChoice(
                index=0,
                message=GatewayMessage(
                    role="assistant",
                    content=[
                        MessageContentPart(
                            type="url",
                            data=UrlContent(
                                url="https://example.invalid/context",
                                crawl=True,
                            ),
                        )
                    ],
                ),
                finish_reason="stop",
            )
        ],
        metadata=ResponseMetaData(provider="gemini"),
    )
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=1024,
    )

    result = await canonicalizer.canonicalize(
        response,
        owner_user_id="user-f7",
    )

    assert result is response
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_p1_preserved_provider_filedata_stays_closed_before_ingest():
    service = _AssetService()
    attachment = GatewayAttachment(
        id="att-provider-filedata",
        filename="generated.png",
        mime_type="image/png",
        uri="https://provider.invalid/generated-file",
        provider_file_id="provider-file-1",
        source="provider",
    )
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=1024,
    )

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await canonicalizer.canonicalize(
            _response(attachments_by_choice=[[attachment]]),
            owner_user_id="user-f7",
        )

    assert exc.value.code == "CAS_F7_SOURCE_CLASS_CLOSED"
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attachment",
    [
        GatewayAttachment(
            id="att-ambiguous-dual-inline",
            filename="generated.bin",
            mime_type="application/octet-stream",
            bytes_data=b"abc",
            base64_data=base64.b64encode(b"abc").decode("ascii"),
            source="base64",
        ),
        GatewayAttachment(
            id="att-ambiguous-remote-id",
            filename="generated.bin",
            mime_type="application/octet-stream",
            base64_data=base64.b64encode(b"abc").decode("ascii"),
            provider_file_id="provider-file-conflict",
            uri="https://provider.invalid/conflict",
            source="base64",
        ),
    ],
)
async def test_f7_p1_rejects_ambiguous_inline_transport_before_ingest(attachment):
    service = _AssetService()
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=1024,
    )

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await canonicalizer.canonicalize(
            _response(attachments_by_choice=[[attachment]]),
            owner_user_id="user-f7",
        )

    assert exc.value.code == "CAS_F7_AMBIGUOUS_INLINE_TRANSPORT"
    assert service.calls == []


def test_gemini_nonstream_preserves_filedata_provenance_for_downstream_reject():
    converter = ResponseChats()
    parts = [
        {
            "fileData": {
                "fileUri": "https://provider.invalid/generated-file",
                "mimeType": "image/png",
            }
        }
    ]

    preserved, _, _ = converter._parse_gemini_parts_to_content(
        parts,
        preserve_generated_file_data=True,
    )
    preserved_data = preserved[0].data
    assert isinstance(preserved_data, GatewayAttachment)
    assert preserved_data.source == "provider"
    assert preserved_data.uri == "https://provider.invalid/generated-file"

    legacy, _, _ = converter._parse_gemini_parts_to_content(parts)
    assert isinstance(legacy[0].data, UrlContent)


def test_f7_p1_provider_runtime_reuses_existing_asset_service_and_bound():
    asset_service = object()
    runtime = ProviderRuntime(circuit_breaker_manager=object())
    context = SimpleNamespace(
        container=SimpleNamespace(asset_service=asset_service),
        config=SimpleNamespace(
            assets=SimpleNamespace(max_upload_bytes=4096),
        ),
    )

    canonicalizer = runtime._build_generated_asset_canonicalizer(context)

    assert canonicalizer._asset_service is asset_service
    assert canonicalizer._max_bytes == 4096
    assert canonicalizer._strict_response_type is True


class _Provider:
    def __init__(self, name):
        self.name = name

    async def has_capability(self, *args, **kwargs):
        return True


class _Routing:
    def __init__(self, providers):
        self.providers = providers

    def get_fallback_chain(self, **kwargs):
        return list(self.providers)


class _Executor:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.retry_policy = SimpleNamespace(max_retries=0)

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute(self, *, provider, **kwargs):
        self.calls.append(provider.name)
        return self.response


class _TerminalCanonicalizer:
    async def canonicalize(self, response, *, owner_user_id):
        raise GeneratedMediaCanonicalizationError(
            "CAS_F7_TERMINAL_TEST",
            "terminal after provider success",
        )


@pytest.mark.asyncio
async def test_f7_p1_canonicalization_failure_does_not_fallback_to_next_provider():
    first = _Provider("first")
    second = _Provider("second")
    executor = _Executor(_response())
    handler = ChatExecutionHandler(
        providers={"first": first, "second": second},
        routing_policy=_Routing([first, second]),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=30,
        generated_asset_canonicalizer=_TerminalCanonicalizer(),
    )

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await handler.execute_with_fallback(
            object(),
            {"model": "logical-model"},
            owner_user_id="user-f7",
        )

    assert exc.value.code == "CAS_F7_TERMINAL_TEST"
    assert executor.calls == ["first"]
