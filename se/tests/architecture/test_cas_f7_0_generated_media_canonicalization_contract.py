from __future__ import annotations

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
        "first future production slice = F7-P1 / PROVIDER-RESPONSE MEDIA ONLY",
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


def test_f7_p1_freezes_one_ingest_attempt_no_internal_retry():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "one canonicalization pass per successful provider response object",
        "no automatic CAS canonicalization retry after ingest has begun",
        "no provider regeneration on CAS failure",
        "ambiguous post-ingest outcome fails closed",
        "no second ingest attempt unless a separately audited replay/idempotency authority can prove the prior canonical result",
        "Content hash is storage-integrity evidence, not ownership or replay authority.",
    ):
        assert phrase in document


def test_streaming_requires_terminal_complete_bounded_object():
    handler = _read("se/src/provider/handlers/chat_handler.py")
    assert "async def stream_with_fallback(" in handler
    assert "stream_started = False" in handler
    assert "stream_started = True" in handler

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "partial provider chunk != terminal generated object != durable FileAsset authority",
        "public Agent response/tool activity event != generated object commitment authority",
        "object completion",
        "deterministic object boundary within the response",
        "content size not exceeding configured AssetStorageSettings.max_upload_bytes",
        "No partially assembled FileAsset may become READY or model/history visible.",
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

    assert "AGENT_STREAM_EVENT_NAMES = (" in stream
    assert "AgentEventName.PROGRESS" in stream
    assert "AgentEventName.TOOL_REQUESTED" in stream
    assert "AgentEventName.TOOL_STARTED" in stream
    assert "AgentEventName.TOOL_COMPLETED" in stream
    assert "AgentEventName.TOOL_FAILED" in stream
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
        "shared post-provider response hook and terminal no-fallback boundary",
        "NO CHANGE in F7-P1",
        "NO CHANGE / NOT ASSET COMMITMENT AUTHORITY",
        "No production file in this table may be edited under F7-0 authority.",
    ):
        assert phrase in normalized


def test_cross_issue_and_closed_authority_boundaries_are_explicit():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "CTX retains Memory/promotion/retrieval/source-discovery and ToolResponsePayload authority.",
        "Agent/R12 retains execution lease/recovery/checkpoint and tool-result commitment authority.",
        "F7-P1 is provider-response-only and does not depend on modifying AgentToolResult persistence.",
        "public Agent response/tool activity event -> CAS generated-asset commitment = NEVER",
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
        "F7-P1 is provider-response-only",
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
