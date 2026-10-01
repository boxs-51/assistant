from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F7_S_STREAMING_GENERATED_MEDIA_CONTRACT_1FC22E26.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _semantic(value: str) -> str:
    return " ".join(value.replace("**", "").replace(chr(96), "").split())


def test_f7_s_contract_is_zero_production_and_keeps_future_authority_closed():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "main@1fc22e2624aaedddb11885c780c1b45b7602321e",
        "production/runtime/schema/migration delta = ZERO",
        "F7-P1 non-stream = LANDED / CANONICAL / HEALTHY",
        "F7-S production CLAIM = CLOSED",
        "F7-T tool-generated media = CLOSED",
        "CAS-F8 = CLOSED",
        "#166 CAS-B1 = HARD HOLD",
        "merge authority = NONE",
    ):
        assert phrase in document


def test_current_stream_handler_yields_provider_chunks_without_f7_s_fence():
    source = _read("se/src/provider/handlers/chat_handler.py")

    assert "async def stream_with_fallback(" in source
    stream = source.index("async def stream_with_fallback(")
    executor = source.index("self.executor.execute_stream(", stream)
    loop = source.index("async for chunk in provider_stream:", executor)
    started = source.index("stream_started = True", loop)
    yielded = source.index("yield chunk", started)
    assert executor < loop < started < yielded

    # F7-P1 canonicalization is present on non-stream but no stream-side
    # canonicalization call exists after execute_stream in the current baseline.
    assert "return await self.generated_asset_canonicalizer.canonicalize(" in source
    stream_body = source[stream:]
    assert "generated_asset_canonicalizer.canonicalize(" not in stream_body

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "contains no generated-media response canonicalization fence on the stream path",
        "generated_media_seen = True",
        "provider attempt becomes fallback-terminal",
        "raw generated-media transport MUST NOT be yielded",
    ):
        assert phrase in document


def test_current_gemini_stream_loses_response_wide_and_filedata_authority():
    source = _read("se/src/provider/gemini/converters/chats/response.py")

    stream = source.index("async def adapt_chat_stream(")
    stream_source = source[stream:]

    assert 'candidate = obj["candidates"][0]' in stream_source
    assert "_parse_gemini_parts_to_content(" in stream_source
    assert "preserve_generated_file_data=True" not in stream_source
    assert "GatewayStreamChunk(" in stream_source
    assert "content_parts=[" in stream_source

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        'processes only obj["candidates"][0]',
        "may lower streaming fileData.fileUri through the legacy URL path",
        "complete logical provider-stream termination",
        "all candidates relevant to generated-media cardinality",
        "Generated fileData must preserve provider-generated provenance",
    ):
        assert phrase in document


def test_gateway_stream_schema_has_no_attachment_delta_field():
    schema = _read("se/src/domain/schemas/response.py")

    delta_start = schema.index("class GatewayStreamDelta(")
    choice_start = schema.index("class GatewayStreamChoice(", delta_start)
    delta = schema[delta_start:choice_start]

    for field in ("content:", "reasoning_content:", "role:", "tool_calls:"):
        assert field in delta
    assert "attachment" not in delta
    assert "asset_id" not in delta

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "GatewayStreamDelta has no attachment/media field",
        "must not invent a raw base64/provider URL text substitute",
        "Any proposal to place generated media in GatewayStreamDelta, emit raw/base64/provider transport as text, or add another public wire field requires a new explicit contract re-freeze.",
    ):
        assert phrase in document


def test_terminal_assembler_commit_point_is_frozen_before_first_ingest():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "Provider-neutral terminal envelope / assembler",
        "response-wide generated-media cardinality",
        "response-wide tool-call presence",
        "cancellation state",
        "The first CAS ingest is prohibited until provider stream completion",
        "generated-media cardinality == 1",
        "exactly one AssetService.ingest_stream(...)",
        "Cardinality greater than one fails closed before first ingest",
        "All deterministic pre-ingest rejection paths require ZERO CAS ingest",
    ):
        assert phrase in document


def test_first_f7_s_source_scope_and_cross_track_fences_are_explicit():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "complete inline generated media with exactly one authoritative inline payload",
        "generated fileData / remote provider handle",
        "generic URL transport",
        "tool-generated media",
        "Issue #166 / CAS-B1 remains HARD HOLD",
        "F7-T/tool-output persistence remains CLOSED",
        "F7-S production CLAIM remains CLOSED until independent audit",
    ):
        assert phrase in document


def test_exact_canonical_stream_output_representation_and_client_parity_gate():
    server_schema = _read("se/src/domain/schemas/response.py")
    client_schema = _read("cl/src/schemas/response.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    assert "content_parts: Optional[List[Dict[str, Any]]] = None" in server_schema
    assert "content_parts: Optional[List[Dict[str, Any]]] = None" not in client_schema

    for phrase in (
        "emits exactly one terminal GatewayStreamChunk",
        "choices[0].delta.content = None for the generated-media object",
        "metadata.content_parts contains exactly one canonical serialized MessageContentPart",
        'the nested GatewayAttachment has source="asset"',
        "the nested attachment carries asset_id and uri=asset://<asset_id>",
        "base64_data, bytes_data and provider_file_id are absent",
        "no second text delta containing asset:// is emitted",
        "Current typed client cl/src/schemas/response.py::ResponseMetaData does not declare content_parts",
        "requires bounded client schema parity by adding optional content_parts",
        "does not create a new public stream field",
    ):
        assert phrase in document
