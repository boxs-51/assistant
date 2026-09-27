from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F7_0_GENERATED_MEDIA_CANONICALIZATION_CONTRACT_3D7FE844.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _normalize(value: str) -> str:
    return " ".join(value.replace("**", "").replace("`", "").split())


def test_f7_0_contract_is_zero_production_and_baseline_locked():
    document = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "main@3d7fe844a94332fb89c23b5d2984041dc14c3f63",
        "Issue #74 comment #5854440946",
        "production/runtime/schema/migration delta = ZERO",
        "CAS-F7-0 contract/evidence preparation = OPEN",
        "CAS-F7 production implementation authority = CLOSED",
        "CAS-F8 = CLOSED",
        "CAS-F6 post-merge Architecture #1452",
    ):
        assert phrase in document


def test_current_gemini_generated_media_gap_is_recorded_as_entry_evidence():
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

    document = _normalize(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "These are baseline observations, not permanent absence assertions.",
        "generated binary/media can leave provider decoding without first receiving canonical CAS identity",
        "Provider/base64/URL/object-store/tool transport identity is never durable CAS identity.",
    ):
        assert phrase in document


def test_shared_direct_agent_provider_boundary_is_positive_source_evidence():
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

    document = _normalize(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "after provider execution has succeeded",
        "before the successful decoded response leaves ChatExecutionHandler",
        "serve both DIRECT and AGENT consumers",
        "MUST NOT persist CAS assets inside Gemini/OpenAI/Ollama-specific converters",
    ):
        assert phrase in document


def test_f7_0_freezes_terminal_streaming_no_partial_asset_semantics():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "partial chunk != terminal generated object != durable FileAsset authority",
        "object completion",
        "complete bounded bytes are available",
        "No partially assembled FileAsset may become READY or model/history visible.",
        "Cancellation or provider error before terminal completion creates no durable visible asset reference.",
        "generated binary/media streaming MUST fail closed for canonical asset creation",
    ):
        assert phrase in document


def test_existing_asset_service_is_frozen_as_canonical_ingestion_authority():
    service = _read("se/src/application/assets/service.py")

    for phrase in (
        "class AssetService:",
        "async def ingest_stream(",
        "owner_user_id: str",
        "max_bytes: Optional[int]",
        'origin_type: str = "USER_UPLOAD"',
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

    document = _normalize(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "F7 MUST reuse this application authority",
        "AssetService.ingest_stream",
        "must not duplicate its SQL/ObjectStorage transaction protocol",
        "current `AssetService.ingest_stream` generates new asset/blob IDs",
        "F7 production MUST NOT assume repeated calls are automatically idempotent",
    ):
        assert phrase in document


def test_tool_generated_media_preserves_existing_committed_result_authority():
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

    document = _normalize(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "AgentToolResult.commit_state != COMMITTED => MUST NOT establish durable/model-visible canonical asset references",
        "F7-T tool-generated canonicalization = DEFERRED / CLOSED",
        "post-hoc rewrite of AgentToolResult.output",
        "change checkpoint/resume/fork semantics",
    ):
        assert phrase in document


def test_f7_0_freezes_canonical_identity_owner_and_transport_neutrality():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        'GatewayAttachment.source = "asset"',
        "GatewayAttachment.uri = asset://<asset_id>",
        "inline base64 payload",
        "provider file ID",
        "provider URI",
        "object-store key",
        "Owner authority is derived from the already-authenticated request/execution context.",
        "No provider field, tool output field, URL, provider file ID, filename, metadata scalar or client-supplied user field may mint or override `owner_user_id`.",
        "If authenticated owner authority is unavailable at the canonicalization point, F7 fails closed",
    ):
        assert phrase in document


def test_f7_0_freezes_future_path_matrix_without_granting_production_edits():
    document = CONTRACT.read_text(encoding="utf-8")

    for path in (
        "se/src/application/assets/generated.py",
        "se/src/application/assets/service.py",
        "se/src/provider/handlers/chat_handler.py",
        "se/src/provider/gemini/converters/chats/response.py",
        "se/src/runtimes/agent/adapters/inference.py",
        "se/src/runtimes/agent/runtime.py",
        "se/src/runtimes/agent/persistence.py",
        "se/src/infrastructure/storage/repositories/agent.py",
    ):
        assert path in document

    normalized = _normalize(document)
    for phrase in (
        "This matrix is a **future production-candidate map**, not a production grant.",
        "EXPECTED NEW / production authority not released",
        "EXPECT NO CHANGE for provider-response slice",
        "NO CHANGE in F7-P1",
        "No production file in this table may be edited under F7-0 authority.",
    ):
        assert phrase in normalized


def test_f7_0_freezes_cross_issue_and_closed_authority_boundaries():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "CTX retains Memory/promotion/retrieval/source-discovery and ToolResponsePayload projection/storage authority.",
        "Agent/R12 retains execution lease/recovery/checkpoint and tool-result commitment authority.",
        "F7-0 migration delta = ZERO",
        "D2A is NON_BLOCKING to this zero-production F7-0 candidate",
        "CAS-F8 legacy cutover/backfill/removal = CLOSED",
        "provider routing/fallback/deadline/model selection = CLOSED",
        "Agent invocation/tool-result commitment state machine = CLOSED",
        "R12 lease/recovery/checkpoint authority = CLOSED",
        "asset-bearing session regeneration = CLOSED",
    ):
        assert phrase in document


def test_f7_0_exit_gate_requires_fresh_audit_before_any_production_claim():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "exact current main",
        "F7-P1 is provider-response-only",
        "tool-generated media is deferred to separate F7-T authority",
        "streaming terminal-object / no-partial-asset semantics",
        "explicit exactly-once/idempotency strategy requirement",
        "fresh exact-head Architecture Linux + Windows",
        "independent audit PASS",
        "blocking F7-0 P0/P1/P2 = NONE",
        "CAS-F7 production CLAIM = CLOSED",
        "Landing this contract does not itself authorize provider/Agent/CAS production edits.",
    ):
        assert phrase in document


def test_generated_ingest_bound_uses_server_asset_configuration():
    settings = _read("se/src/infrastructure/config/schemas.py")
    service = _read("se/src/application/assets/service.py")
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    assert "class AssetStorageSettings(BaseModel):" in settings
    assert "max_upload_bytes: int = Field(default=268_435_456, gt=0)" in settings
    assert "max_bytes: Optional[int]" in service
    assert "observed_bytes > max_bytes" in service

    for phrase in (
        "F7 generated_ingest_max_bytes = current configured AssetStorageSettings.max_upload_bytes",
        "268_435_456 bytes / 256 MiB",
        "F6 desktop 32 MiB canonical media render-memory ceiling is a separate client/UI safety rule",
        "MUST NOT be reused as the server F7 ingestion limit",
    ):
        assert phrase in document


def test_provider_response_origin_type_mapping_is_exact_and_schema_compatible():
    model = _read("se/src/infrastructure/storage/models/sql/assets/file.py")
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

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

    for phrase in (
        "provider-response generated media emitted as assistant output | ASSISTANT | F7-P1 frozen mapping",
        "tool-result generated media | TOOL | reserved for future F7-T only; production CLOSED",
        "ordinary F6 user upload | USER_UPLOAD | existing behavior; not re-ingested by F7",
        "No schema/migration change is authorized by F7-0.",
    ):
        assert phrase in document


def test_post_provider_canonicalization_is_terminal_to_fallback_and_breakers():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

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


def test_tool_generated_media_is_explicitly_deferred_without_committed_rewrite():
    persistence = _read("se/src/runtimes/agent/persistence.py")
    runtime = _read("se/src/runtimes/agent/runtime.py")
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "async def save_tool_result(",
        'values["commit_state"] = "COMMITTED"',
        'values["commit_state"] = "PROVISIONAL"',
        '"TERMINAL_COMMITTED"',
    ):
        assert phrase in persistence
    assert "save_tool_result(" in runtime

    for phrase in (
        "AgentToolResult.commit_state != COMMITTED => MUST NOT establish durable/model-visible canonical asset references",
        "AgentToolResult.output durable JSON and COMMITTED projection terminal/immutable",
        "F7-P1 tool-generated media support = NONE",
        "F7-T tool-generated canonicalization = DEFERRED / CLOSED",
        "post-hoc rewrite of AgentToolResult.output",
        "tool-generated dual-representation is why F7-T remains CLOSED",
    ):
        assert phrase in document


def test_current_main_agent_activity_stream_is_not_asset_commit_authority():
    stream = _read("se/src/runtimes/agent/stream.py")
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    assert stream
    for phrase in (
        "Agent activity event != generated object commitment authority",
        "NO CHANGE / NOT ASSET COMMITMENT AUTHORITY",
        "Agent activity event -> CAS generated-asset commitment = NEVER",
    ):
        assert phrase in document


def test_first_provider_slice_freezes_one_ingest_attempt_no_internal_retry():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "one canonicalization pass per successful provider response object",
        "no automatic CAS canonicalization retry after ingest has begun",
        "no provider regeneration on CAS failure",
        "ambiguous post-ingest outcome fails closed",
        "no second ingest attempt unless a separately audited replay/idempotency authority can prove the prior canonical result",
    ):
        assert phrase in document
