from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from se.src.application.assets.generated import (
    GeneratedAssetCanonicalizer,
    GeneratedAssetStreamAssembler,
    GeneratedMediaCanonicalizationError,
)
from se.src.domain.schemas import (
    FunctionCall,
    GatewayAttachment,
    GatewayStreamChoice,
    GatewayStreamChunk,
    GatewayStreamDelta,
    GatewayToolCall,
    MessageContentPart,
    ResponseMetaData,
)
from se.src.domain.schemas.attachment import UrlContent
from se.src.provider.exceptions import ProviderUnavailableError
from se.src.provider.gemini.converters.chats.response import ResponseChats
from se.src.provider.handlers.chat_handler import ChatExecutionHandler


class _AssetService:
    def __init__(self):
        self.calls = []

    async def ingest_stream(self, **kwargs):
        payload = b"".join([chunk async for chunk in kwargs["stream"]])
        call = dict(kwargs)
        call.pop("stream")
        call["payload"] = payload
        self.calls.append(call)
        return SimpleNamespace(
            asset_id="asset-f7-s",
            filename=kwargs["filename"],
            mime_type=kwargs["mime_type"],
            size_bytes=len(payload),
            sha256="b" * 64,
            state="READY",
            uri="asset://asset-f7-s",
        )


def _inline_attachment(
    payload: bytes = b"stream-generated",
    *,
    uri: str | None = None,
) -> GatewayAttachment:
    return GatewayAttachment(
        id="att-f7-s",
        filename="generated.bin",
        mime_type="application/octet-stream",
        base64_data=base64.b64encode(payload).decode("ascii"),
        uri=uri,
        source="base64",
    )


def _provider_attachment() -> GatewayAttachment:
    return GatewayAttachment(
        id="att-provider-f7-s",
        filename="generated.png",
        mime_type="image/png",
        uri="https://provider.invalid/generated",
        provider_file_id="provider-generated-1",
        source="provider",
    )


def _part_dict(
    attachment: GatewayAttachment,
    *,
    candidate_index: int = 0,
):
    dumped = MessageContentPart(
        type="file",
        data=attachment,
    ).model_dump(mode="json", exclude_none=True)
    if candidate_index:
        dumped["_cas_f7_candidate_index"] = candidate_index
    return dumped


def _chunk(
    *,
    content: str | None = None,
    content_parts=None,
    tool_calls=None,
    finish_reason: str | None = None,
    usage=None,
) -> GatewayStreamChunk:
    return GatewayStreamChunk(
        id="stream-f7-s",
        model="model-f7-s",
        choices=[
            GatewayStreamChoice(
                index=0,
                delta=GatewayStreamDelta(
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls,
                ),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
        metadata=ResponseMetaData(
            provider="gemini",
            provider_response_id="provider-stream-f7-s",
            content_parts=content_parts,
        ),
    )


def _assembler(service=None):
    service = service or _AssetService()
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=4096,
    )
    return (
        GeneratedAssetStreamAssembler(
            canonicalizer,
            owner_user_id="user-f7-s",
        ),
        service,
    )


def test_f7_s_text_only_stream_remains_incremental_and_zero_ingest():
    assembler, service = _assembler()
    chunk = _chunk(content="hello")

    assert assembler.observe(chunk) is chunk
    assert assembler.media_seen is False
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_s_single_inline_media_withholds_transport_then_ingests_once():
    assembler, service = _assembler()
    media = _chunk(
        content="prefix",
        content_parts=[_part_dict(_inline_attachment(b"abc"))],
    )

    public = assembler.observe(media)
    assert public is not None
    assert public.choices[0].delta.content == "prefix"
    assert public.metadata.content_parts is None
    assert service.calls == []

    # Provider terminal completion is observed separately.
    assert assembler.observe(_chunk(finish_reason="stop")) is None
    final = await assembler.finalize()

    assert len(service.calls) == 1
    assert service.calls[0]["payload"] == b"abc"
    assert final is not None
    assert final.choices[0].finish_reason == "stop"
    assert final.choices[0].delta.content is None
    assert final.choices[0].delta.reasoning_content is None
    assert final.choices[0].delta.tool_calls is None
    assert final.metadata.raw_response is None
    assert len(final.metadata.content_parts or []) == 1

    part = MessageContentPart.model_validate(final.metadata.content_parts[0])
    attachment = part.data
    assert isinstance(attachment, GatewayAttachment)
    assert attachment.source == "asset"
    assert attachment.asset_id == "asset-f7-s"
    assert attachment.uri == "asset://asset-f7-s"
    assert attachment.base64_data is None
    assert attachment.bytes_data is None
    assert attachment.provider_file_id is None


@pytest.mark.asyncio
async def test_f7_s_multi_object_across_chunks_rejects_before_first_ingest():
    assembler, service = _assembler()
    assembler.observe(
        _chunk(content_parts=[_part_dict(_inline_attachment(b"a"))])
    )
    assembler.observe(
        _chunk(content_parts=[_part_dict(_inline_attachment(b"b"))])
    )
    assembler.observe(_chunk(finish_reason="stop"))

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await assembler.finalize()

    assert exc.value.code == "CAS_F7_MULTI_OBJECT_UNSUPPORTED"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_s_non_selected_candidate_media_rejects_before_ingest():
    assembler, service = _assembler()
    assembler.observe(
        _chunk(
            content_parts=[
                _part_dict(
                    _inline_attachment(),
                    candidate_index=1,
                )
            ]
        )
    )
    assembler.observe(_chunk(finish_reason="stop"))

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await assembler.finalize()

    assert exc.value.code == "CAS_F7_NON_SELECTED_CHOICE_MEDIA"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_s_media_plus_tool_call_rejects_before_ingest():
    assembler, service = _assembler()
    assembler.observe(
        _chunk(content_parts=[_part_dict(_inline_attachment())])
    )
    tool_call = GatewayToolCall(
        id="call-f7-s",
        function=FunctionCall(name="tool.read", arguments="{}"),
    )
    # Tool call is withheld after media observation but remains terminal authority.
    assert assembler.observe(_chunk(tool_calls=[tool_call])) is None
    assembler.observe(_chunk(finish_reason="tool_calls"))

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await assembler.finalize()

    assert exc.value.code == "CAS_F7_NONTERMINAL_TOOL_CALL_MEDIA"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_s_filedata_provider_source_remains_closed_zero_ingest():
    assembler, service = _assembler()
    assembler.observe(
        _chunk(content_parts=[_part_dict(_provider_attachment())])
    )
    assembler.observe(_chunk(finish_reason="stop"))

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await assembler.finalize()

    assert exc.value.code == "CAS_F7_SOURCE_CLASS_CLOSED"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_s_ambiguous_inline_transport_rejects_zero_ingest():
    assembler, service = _assembler()
    assembler.observe(
        _chunk(
            content_parts=[
                _part_dict(
                    _inline_attachment(
                        b"abc",
                        uri="https://provider.invalid/conflict",
                    )
                )
            ]
        )
    )
    assembler.observe(_chunk(finish_reason="stop"))

    with pytest.raises(GeneratedMediaCanonicalizationError) as exc:
        await assembler.finalize()

    assert exc.value.code == "CAS_F7_AMBIGUOUS_INLINE_TRANSPORT"
    assert service.calls == []


@pytest.mark.asyncio
async def test_f7_s_ordinary_url_stream_is_pass_through_zero_ingest():
    assembler, service = _assembler()
    url_part = MessageContentPart(
        type="url",
        data=UrlContent(url="https://example.invalid/context"),
    ).model_dump(mode="json", exclude_none=True)
    chunk = _chunk(
        content="url context",
        content_parts=[url_part],
        finish_reason="stop",
    )

    assert assembler.observe(chunk) is chunk
    assert await assembler.finalize() is None
    assert service.calls == []


class _StreamingResponse:
    def __init__(self, objects):
        self._payload = "".join(json.dumps(obj) for obj in objects).encode("utf-8")

    async def aiter_bytes(self):
        yield self._payload


@pytest.mark.asyncio
async def test_gemini_stream_preserves_filedata_and_nonselected_media_provenance():
    converter = ResponseChats()
    response = _StreamingResponse(
        [
            {
                "modelVersion": "gemini-test",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "fileData": {
                                        "fileUri": "https://provider.invalid/generated",
                                        "mimeType": "image/png",
                                    }
                                }
                            ]
                        }
                    },
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(b"second").decode("ascii"),
                                    }
                                }
                            ]
                        }
                    },
                ],
            }
        ]
    )

    chunks = [
        chunk
        async for chunk in converter.adapt_chat_stream(response)
    ]

    assert len(chunks) == 1
    parts = chunks[0].metadata.content_parts or []
    assert len(parts) == 2

    selected = MessageContentPart.model_validate(parts[0])
    selected_attachment = selected.data
    assert isinstance(selected_attachment, GatewayAttachment)
    assert selected_attachment.source == "provider"

    assert parts[1]["_cas_f7_candidate_index"] == 1
    nonselected_payload = dict(parts[1])
    nonselected_payload.pop("_cas_f7_candidate_index")
    nonselected = MessageContentPart.model_validate(nonselected_payload)
    nonselected_attachment = nonselected.data
    assert isinstance(nonselected_attachment, GatewayAttachment)
    assert nonselected_attachment.source == "base64"


class _Provider:
    def __init__(self, name):
        self.name = name
        self.probes = 0

    async def has_capability(self, *args, **kwargs):
        self.probes += 1
        return True


class _Routing:
    def __init__(self, providers):
        self.providers = list(providers)

    def get_fallback_chain(self, **kwargs):
        return list(self.providers)


class _StreamExecutor:
    def __init__(self, outcomes):
        self.outcomes = {name: list(values) for name, values in outcomes.items()}
        self.calls = []
        self.retry_policy = SimpleNamespace(max_retries=0)

    async def is_provider_healthy(self, provider_name):
        return True

    async def execute_stream(self, *, provider, **kwargs):
        self.calls.append(provider.name)
        for item in self.outcomes[provider.name]:
            if isinstance(item, BaseException):
                raise item
            yield item


@pytest.mark.asyncio
async def test_f7_s_media_observation_makes_provider_failure_fallback_terminal():
    first = _Provider("first")
    second = _Provider("second")
    service = _AssetService()
    canonicalizer = GeneratedAssetCanonicalizer(
        asset_service=service,
        max_bytes=4096,
    )
    error = ProviderUnavailableError(
        "provider failed after media",
        provider_name="first",
    )
    executor = _StreamExecutor(
        {
            "first": [
                _chunk(content_parts=[_part_dict(_inline_attachment())]),
                error,
            ],
            "second": [_chunk(content="must-not-run", finish_reason="stop")],
        }
    )
    handler = ChatExecutionHandler(
        providers={"first": first, "second": second},
        routing_policy=_Routing([first, second]),
        executor=executor,
        circuit_breaker_manager=object(),
        timeout=30,
        generated_asset_canonicalizer=canonicalizer,
    )

    with pytest.raises(ProviderUnavailableError):
        async for _ in handler.stream_with_fallback(
            object(),
            {"model": "logical-model"},
            owner_user_id="user-f7-s",
        ):
            pass

    assert executor.calls == ["first"]
    assert second.probes == 0
    assert service.calls == []
