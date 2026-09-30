from __future__ import annotations

import ast
from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F7_0_GENERATED_MEDIA_CANONICALIZATION_CONTRACT_ECB7E5DC.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _semantic(value: str) -> str:
    return " ".join(value.replace("**", "").replace(chr(96), "").split())


def test_replacement_contract_is_zero_production_and_current_main_locked():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "main@ecb7e5dc5c61aba9772d9f6ebfec303ce8a01755",
        "production/runtime/schema/migration delta = ZERO",
        "first future production slice = F7-P1 / NON-STREAM PROVIDER-RESPONSE MEDIA ONLY",
        "CAS-F7-P1 production CLAIM = CLOSED",
        "tool-generated media production = DEFERRED / CLOSED",
        "CAS-F8 = CLOSED",
        "merge authority = NONE",
    ):
        assert phrase in document


def test_current_provider_gap_is_positive_entry_evidence_only():
    source = _read("se/src/provider/gemini/converters/chats/response.py")

    for phrase in (
        'elif "inlineData" in part:',
        'inline_data.get("data", "")',
        "GatewayAttachment(",
        "base64_data=base64_data",
        'source="base64"',
        '"fileData" in part',
        'part["fileData"].get("fileUri", "")',
    ):
        assert phrase in source

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "These are baseline observations, not permanent absence assertions.",
        "provider-generated binary/media can leave provider decoding without first receiving canonical CAS identity",
        "Provider converters remain provider decoding/lowering owners.",
    ):
        assert phrase in document


def test_shared_direct_agent_provider_boundary_is_frozen():
    handler = _read("se/src/provider/handlers/chat_handler.py")
    inference = _read("se/src/runtimes/agent/adapters/inference.py")

    assert "async def execute_with_fallback(" in handler
    assert "return await self.executor.execute(" in handler
    assert "async def stream_with_fallback(" in handler
    assert "async for chunk in provider_stream:" in handler
    assert "yield chunk" in handler

    assert 'handler = getattr(self._provider_runtime, "chat_handler", None)' in inference
    assert "handler.execute_with_fallback(" in inference
    assert "gateway_message = choice.message" in inference
    assert "content=jsonable(gateway_message.content)" in inference

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "provider execution has succeeded",
        "before the successful decoded response leaves ChatExecutionHandler",
        "DIRECT return",
        "ProviderInferenceAdapter -> AGENT InferenceResponse",
        "F7-P1 MUST NOT persist CAS assets inside Gemini/OpenAI/Ollama-specific converters.",
    ):
        assert phrase in document


def test_post_provider_canonicalization_failure_is_terminal_to_fallback():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "canonicalization failure is terminal to that logical chat execution",
        "select provider B",
        "retry provider generation",
        "re-enter provider fallback",
        "provider circuit-breaker health",
        "mark provider A unhealthy solely because CAS canonicalization failed",
        "produce a second generated response/media object",
        "not interpreted as ProviderError or transport failure by provider fallback policy",
    ):
        assert phrase in document


def test_generated_ingest_bound_uses_server_asset_configuration():
    settings = _read("se/src/infrastructure/config/schemas.py")
    service = _read("se/src/application/assets/service.py")

    assert "class AssetStorageSettings(BaseModel):" in settings
    assert "max_upload_bytes: int = Field(default=268_435_456, gt=0)" in settings
    assert "max_bytes: Optional[int]" in service
    assert "observed_bytes > max_bytes" in service

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "F7 generated_ingest_max_bytes = current configured AssetStorageSettings.max_upload_bytes",
        "268_435_456 bytes / 256 MiB",
        "F6 desktop 32 MiB canonical media render-memory ceiling is a separate client/UI safety rule",
        "MUST NOT be reused as the server F7 ingestion limit",
    ):
        assert phrase in document


def test_existing_asset_service_remains_storage_finalize_authority():
    service = _read("se/src/application/assets/service.py")

    for phrase in (
        "class AssetService:",
        "async def ingest_stream(",
        "owner_user_id: str",
        'if not owner_user_id:',
        'raise ValueError("owner_user_id is required")',
        '"state": "STAGING"',
        "result.size_bytes",
        "result.sha256",
        'blob_record.state = "READY"',
        'expected_state="STAGING"',
        'values={"state": "READY"}',
        "_abort_staging_ingest(",
        'uri=f"asset://{file_record.id}"',
    ):
        assert phrase in service

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "F7 MUST reuse this application authority rather than persist FileAsset/FileBlob directly",
        "AssetService.ingest_stream remains the storage/finalize authority",
        "Current AssetService.ingest_stream allocates new asset/blob IDs and is not a content-idempotency primitive.",
    ):
        assert phrase in document


def test_provider_response_origin_type_mapping_is_exact_and_schema_compatible():
    model = _read("se/src/infrastructure/storage/models/sql/assets/file.py")

    for phrase in (
        "USER_UPLOAD",
        "ASSISTANT",
        "TOOL",
        "PROVIDER_IMPORT",
        "SYSTEM_IMPORT",
        "LEGACY_MIGRATION",
        "ck_files_origin_type",
    ):
        assert phrase in model

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "provider-response generated media emitted as assistant output | ASSISTANT | F7-P1 frozen mapping",
        "tool-result generated media | TOOL | reserved for future F7-T only; production CLOSED",
        "ordinary F6 user upload | USER_UPLOAD | existing behavior; not re-ingested by F7",
        "No schema/migration change is authorized by F7-0.",
    ):
        assert phrase in document


def test_f7_p1_rejects_multi_object_response_before_first_ingest():
    gemini = _read("se/src/provider/gemini/converters/chats/response.py")
    assert "for part in parts:" in gemini
    assert 'elif "inlineData" in part:' in gemini
    assert "content_parts.append(MessageContentPart(" in gemini

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "response-wide generated-media cardinality preflight",
        "at most ONE durable generated-media object in the consumer-selected choice of a terminal non-stream assistant response with no tool calls",
        "fail closed BEFORE first CAS ingest",
        "ZERO CAS ingest attempts",
        "ZERO READY assets created for that logical response",
        "multiple Gemini inlineData parts or candidates",
        "choice index 0 as the only consumer-selected choice eligible to carry canonicalized generated media",
        "If any generated-media candidate is present in choice index >0, the response fails closed before the first CAS ingest",
        "multi-object canonicalization requires a separately audited atomic-batch or compensation authority",
        "MUST NOT infer READY deletion, cleanup or rollback authority",
        "MUST NOT trigger provider fallback/regeneration",
    ):
        assert phrase in document


def test_f7_p1_rejects_generated_media_on_nonterminal_tool_call_response():
    direct = _read("se/src/runtimes/chat/direct.py")
    agent = _read("se/src/runtimes/agent/runtime.py")

    for phrase in (
        "transcript.append(response.message)",
        "calls = list(response.message.tool_calls)",
        "if not calls:",
        "return response",
    ):
        assert phrase in direct

    for phrase in (
        "if not response.message.tool_calls:",
        "record.close(AgentLoopState.FINALIZING)",
        "record.close(AgentLoopState.COMPLETED)",
        "record.state = transition(record.state, AgentLoopState.TOOL_CALLING)",
    ):
        assert phrase in agent

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "terminal non-stream assistant response with no tool calls",
        "AND response.message.tool_calls is empty",
        "any durable generated-media object on a response with one or more tool_calls",
        "nonterminal intermediate response in the initial F7-P1 slice",
        "fail closed BEFORE first CAS ingest",
        "ZERO CAS ingest attempts",
        "ZERO READY assets created for that logical response",
        "DIRECT appends each inference response but returns it only when response.message.tool_calls is empty",
        "AGENT likewise transitions to FINALIZING and COMPLETED only when response.message.tool_calls is empty",
        "F7-P1 MUST NOT create a READY asset from an intermediate tool-loop response",
        "Supporting generated media on tool-call-bearing intermediate responses is deferred.",
        "terminal response with empty tool_calls",
        "generated media on any response with non-empty tool_calls fails closed before the first CAS ingest with ZERO CAS ingest attempts / ZERO READY assets",
    ):
        assert phrase in document


def test_f7_p1_nonstream_filedata_requires_preserve_or_reject_compatibility():
    gemini = _read("se/src/provider/gemini/converters/chats/response.py")

    for phrase in (
        "async def adapt_chat(",
        "_parse_gemini_parts_to_content(",
        '"fileData" in part',
        "UrlContent(url=url_str, crawl=True)",
    ):
        assert phrase in gemini

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    executor = _read("se/src/provider/executor.py")
    handler = _read("se/src/provider/handlers/chat_handler.py")
    assert "except Exception as e:" in executor
    assert "await breaker.on_failure()" in executor
    assert "normalized = wrap_provider_exception(e, provider.name)" in executor
    assert "except (" in handler
    assert "ProviderError," in handler
    assert "continue" in handler

    for phrase in (
        "For provider fileData, generic URL or remote handle in the initial F7-P1 slice:",
        "support = EXCLUDED / CLOSED",
        "F7-P1 MUST NOT silently treat a generic UrlContent as ordinary non-generated content when the provider source was generated fileData",
        "MUST ALWAYS fail closed before the first CAS ingest in the initial F7-P1 slice, even when generated-media provenance is preserved",
        "provenance preservation != admission",
        "provenance preservation != canonicalization authority",
        "ZERO CAS ingest attempts / ZERO READY assets",
        "no raw provider URI may escape as durable message/history identity or as a substitute for canonical CAS identity",
        "converter-side rejection is not a safe terminal boundary",
        "breaker.on_failure()",
        "continue to another provider",
        "preserve generated fileData provenance/identity through successful provider decoding",
        "run the shared provider-neutral generated-media preflight after provider SUCCESS and outside provider fallback",
        "converter-side rejection before provenance loss is NOT an allowed implementation shortcut",
        "not to affect provider breaker health and not to trigger fallback/regeneration",
        "A separate future source-class release is required before any of those transports may be canonicalized.",
        "REQUIRED future F7-P1 compatibility change to preserve non-stream generated fileData provenance through successful decode",
        "downstream provider-neutral preflight performs terminal rejection while fileData remains EXCLUDED/CLOSED",
    ):
        assert phrase in document

    assert "reject unsupported generated fileData responses in the provider/envelope layer before that provenance is lost" not in document
    assert "whose provenance is not preserved before generic lowering" not in document
    assert "provider URL/handle that cannot resolve to complete bounded bytes" not in document


def test_f7_p1_rejects_generated_media_outside_selected_choice_before_ingest():
    inference = _read("se/src/runtimes/agent/adapters/inference.py")
    gemini = _read("se/src/provider/gemini/converters/chats/response.py")

    assert "choice = response.choices[0]" in inference
    assert "for idx, candidate in enumerate(response_data.get(\"candidates\", [])):" in gemini
    assert "choices.append(GatewayChoice(" in gemini

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "choice index 0 as the only consumer-selected choice eligible to carry canonicalized generated media",
        "If any generated-media candidate is present in choice index >0, the response fails closed before the first CAS ingest",
        "ZERO CAS ingest attempts",
        "F7-P1 MUST NOT create a READY asset that the current consumer projection would immediately discard",
        "F7-P1 preflight must reject generated media in choice index >0 before ingest",
    ):
        assert phrase in document


def test_f7_p1_preflight_is_nonstream_only_without_terminal_stream_option():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    assert (
        "response-wide generated-media cardinality preflight over the fully decoded successful "
        "NON-STREAM provider response only"
    ) in document
    assert "or over the terminally assembled provider stream" not in document


def test_f7_p1_freezes_one_ingest_attempt_no_internal_retry():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "at most ONE durable generated-media object is eligible per terminal successful non-stream assistant response, it must be in choice index 0, and response.message.tool_calls must be empty",
        "response cardinality greater than one fails closed before the first CAS ingest with ZERO CAS ingest attempts",
        "one canonicalization pass for the sole admitted generated-media object",
        "no automatic CAS canonicalization retry after ingest has begun",
        "no provider regeneration on CAS failure",
        "ambiguous post-ingest outcome fails closed",
        "no second ingest attempt unless a separately audited replay/idempotency authority can prove the prior canonical result",
        "Content hash is storage-integrity evidence, not ownership or replay authority.",
    ):
        assert phrase in document


def test_streaming_generated_media_is_explicitly_deferred_from_first_slice():
    handler = _read("se/src/provider/handlers/chat_handler.py")
    gemini = _read("se/src/provider/gemini/converters/chats/response.py")

    assert "async def stream_with_fallback(" in handler
    assert 'candidate = obj["candidates"][0]' in gemini
    assert 'type="url"' in gemini
    assert "UrlContent(url=url_str, crawl=True)" in gemini

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "The initial F7-P1 production slice is non-stream only.",
        "F7-S streaming generated media = DEFERRED / CLOSED",
        "selects only obj[\"candidates\"][0]",
        "extensionless Gemini fileData.fileUri may be lowered to generic UrlContent",
        "no streaming generated-media response is eligible for CAS canonicalization under the first F7-P1 production slice",
        "MUST NOT rely on a downstream canonicalizer to reconstruct candidate cardinality or media identity",
        "separate REQUIRED future F7-S change for streaming",
        "partial provider chunk != terminal generated object != durable FileAsset authority",
        "public Agent response/tool activity event != generated object commitment authority",
        "MUST NOT trigger provider fallback/regeneration",
    ):
        assert phrase in document


def test_tool_generated_media_is_deferred_without_committed_result_rewrite():
    persistence = _read("se/src/runtimes/agent/persistence.py")
    runtime = _read("se/src/runtimes/agent/runtime.py")

    for phrase in (
        "async def save_tool_result(",
        'values["commit_state"] = "COMMITTED"',
        'values["commit_state"] = "PROVISIONAL"',
        '"TERMINAL_COMMITTED"',
        "model-consumable only after R6 marks its outcome TERMINAL_COMMITTED",
    ):
        assert phrase in persistence
    assert "save_tool_result(" in runtime

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "AgentToolResult.commit_state != COMMITTED => MUST NOT establish durable/model-visible canonical asset references",
        "AgentToolResult.output durable JSON and COMMITTED projection terminal/immutable",
        "F7-P1 tool-generated media support = NONE",
        "F7-T tool-generated canonicalization = DEFERRED / CLOSED",
        "post-hoc rewrite of AgentToolResult.output",
        "tool-generated dual-representation is why F7-T remains CLOSED",
    ):
        assert phrase in document


def test_current_main_public_agent_stream_is_response_tool_only_and_not_asset_authority():
    stream = _read("se/src/runtimes/agent/stream.py")
    runtime = _read("se/src/runtimes/agent/runtime.py")
    docs = _read("docs/agent_activity_stream.md")

    tree = ast.parse(stream)
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "AGENT_STREAM_EVENT_NAMES"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    value = assignments[0].value
    assert isinstance(value, ast.Tuple)
    actual_members = tuple(
        f"{element.value.id}.{element.attr}"
        if isinstance(element, ast.Attribute)
        and isinstance(element.value, ast.Name)
        else None
        for element in value.elts
    )
    assert actual_members == (
        "AgentEventName.PROGRESS",
        "AgentEventName.TOOL_REQUESTED",
        "AgentEventName.TOOL_STARTED",
        "AgentEventName.TOOL_COMPLETED",
        "AgentEventName.TOOL_FAILED",
    )
    assert 'event_type="agent.response" if name == AgentEventName.PROGRESS else name' in stream
    assert 'channel: Literal["response", "tool"]' in stream
    assert "CONTEXT_READY" not in runtime
    assert "No lifecycle or private model" in docs
    assert "reasoning is sent to the UI." in docs

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "CONTEXT_READY public lifecycle event = REMOVED / NOT CANONICAL",
        "public Agent stream = response/tool only",
        "AgentEventName.PROGRESS -> public agent.response",
        "lifecycle/private reasoning -> NOT SENT TO UI",
        "NO CHANGE / NOT ASSET COMMITMENT AUTHORITY",
        "public Agent response/tool activity event -> CAS generated-asset commitment = NEVER",
    ):
        assert phrase in document


def test_provider_response_identity_and_owner_are_transport_neutral():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        'GatewayAttachment.source = "asset"',
        "GatewayAttachment.uri = asset://<asset_id>",
        "inline base64 payload",
        "provider file ID",
        "provider URI",
        "Owner authority is derived only from the already-authenticated request/execution context.",
        "may mint or override owner_user_id",
        "If authenticated owner authority is unavailable at the canonicalization point, F7-P1 fails closed",
    ):
        assert phrase in document


def test_future_path_matrix_is_explicit_and_zero_production():
    document = CONTRACT.read_text(encoding="utf-8")

    for path in (
        "se/src/application/assets/generated.py",
        "se/src/application/assets/service.py",
        "se/src/infrastructure/config/schemas.py",
        "se/src/infrastructure/storage/models/sql/assets/file.py",
        "se/src/provider/handlers/chat_handler.py",
        "se/src/provider/gemini/converters/chats/response.py",
        "se/src/runtimes/agent/adapters/inference.py",
        "se/src/runtimes/chat/direct.py",
        "se/src/runtimes/agent/runtime.py",
        "se/src/runtimes/agent/persistence.py",
        "se/src/runtimes/agent/stream.py",
        "se/src/infrastructure/storage/repositories/agent.py",
    ):
        assert path in document

    normalized = _semantic(document)
    for phrase in (
        "This is a future production-candidate map, not a production grant.",
        "EXPECTED NEW / production authority not released",
        "non-stream post-provider response hook and terminal no-fallback boundary",
        "REQUIRED future F7-P1 compatibility change to preserve non-stream generated fileData provenance through successful decode",
        "NO CHANGE / NOT ASSET COMMITMENT AUTHORITY",
        "No production file in this table may be edited under F7-0 authority.",
    ):
        assert phrase in normalized


def test_cross_issue_and_closed_authority_boundaries_are_explicit():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "CTX retains Memory/promotion/retrieval/source-discovery and ToolResponsePayload authority.",
        "Agent/R12 retains execution lease/recovery/checkpoint and tool-result commitment authority.",
        "F7-P1 is non-stream provider-response-only and does not depend on modifying AgentToolResult persistence. F7-S streaming generated media remains separately CLOSED.",
        "public Agent response/tool activity event -> CAS generated-asset commitment = NEVER",
        "CAS-F7-S streaming generated media = CLOSED",
        "CAS-F7-T tool-generated media = CLOSED",
        "CAS-F8 legacy cutover/backfill/removal = CLOSED",
        "provider routing/fallback/deadline/model selection = CLOSED",
        "asset-bearing session regeneration = CLOSED",
    ):
        assert phrase in document


def test_f7_0_exit_gate_keeps_production_closed_until_replacement_green():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "exact current main",
        "F7-P1 is non-stream provider-response-only",
        "non-stream complete-object boundary, terminal-response empty-tool_calls fence, selected-choice-0 fence and response-wide cardinality fence",
        "generated fileData/URL/remote-handle forms remain EXCLUDED/CLOSED, provenance must survive successful decode, and terminal rejection occurs only in provider-neutral post-success preflight before first CAS ingest",
        "streaming generated media is deferred to separate F7-S authority",
        "tool-generated media is deferred to separate F7-T authority",
        "provider success -> CAS canonicalization failure is terminal/no-fallback/no-breaker",
        "AssetStorageSettings.max_upload_bytes is the authoritative server ingestion bound",
        'origin_type="ASSISTANT" for F7-P1 provider-generated assistant media',
        "one-ingest-attempt/no-internal-retry rule",
        "exact current-main health GREEN/GREEN and fresh exact-head Architecture Linux + Windows GREEN",
        "independent replacement audit PASS",
        "blocking F7-0 P0/P1/P2 = NONE",
        "CAS-F7-P1 production CLAIM = CLOSED",
    ):
        assert phrase in document


def test_issue_134_and_reserved_agent_asset_grants_do_not_transfer_cas_authority():
    roadmap = _read("docs/central_asset/CAS_AGENT_ASSET_GRANTS_ROADMAP.md")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    assert "State:** `RESERVED / NOT OPEN`" in roadmap
    assert "Implementation authority:** none" in roadmap
    assert "without altering active CAS-F6/F7/F8 authority" in roadmap

    for phrase in (
        "Issue #134 is CLOSED with resolution RESOLVED BY SUPERSEDING CANONICAL CONTRACT.",
        "State = RESERVED / NOT OPEN",
        "Implementation authority = none",
        "effect on active CAS-F7/F8 authority = NONE",
        "It does not open grant APIs, schema, provider behavior, or any production path.",
    ):
        assert phrase in document
