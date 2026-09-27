from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F7_0_GENERATED_MEDIA_CANONICALIZATION_CONTRACT_4E1B276A.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _normalize(value: str) -> str:
    return " ".join(value.split())


def test_f7_0_contract_is_zero_production_and_baseline_locked():
    document = CONTRACT.read_text(encoding="utf-8")

    for phrase in (
        "main@4e1b276aa2c3bbb481af923326c3a9cb73195906",
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
        "after `ProviderExecutor.execute(...)` returns a fully decoded `GatewayResponse`",
        "before `ChatExecutionHandler.execute_with_fallback(...)` returns that response",
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
        "only after existing Agent authority has produced or loaded a COMMITTED tool result",
        "F7 MUST NOT: - set or promote `commit_state`",
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
        "CONDITIONAL future change; separate tool-generated production slice may be required",
        "No production file in this table may be edited under F7-0 authority.",
    ):
        assert phrase in normalized


def test_f7_0_freezes_cross_issue_and_closed_authority_boundaries():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "CTX retains Memory/promotion/retrieval/source-discovery and ToolResponsePayload projection/storage authority.",
        "Current AE-R12-D2A PR #130 is a separate Draft candidate",
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
        "DIRECT + AGENT provider-response ownership",
        "COMMITTED-only tool-result fence",
        "streaming terminal-object / no-partial-asset semantics",
        "explicit exactly-once/idempotency strategy requirement",
        "fresh exact-head Architecture Linux + Windows",
        "independent audit PASS",
        "blocking F7-0 P0/P1/P2 = NONE",
        "CAS-F7 production CLAIM = CLOSED",
        "Landing this contract does not itself authorize provider/Agent/CAS production edits.",
    ):
        assert phrase in document
