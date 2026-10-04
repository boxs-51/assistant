from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F7_T_TOOL_GENERATED_MEDIA_CONTRACT_522B543E.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _semantic(value: str) -> str:
    return " ".join(value.replace("**", "").replace(chr(96), "").split())


def test_f7_t_contract_is_zero_production_and_authority_remains_closed():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "main@522b543e66f309085729e57a2248258b7c4f2599",
        "production/runtime/schema/migration delta = ZERO",
        "production PRE-CLAIM PASS = NOT YET",
        "production CLAIM = NONE",
        "merge authority = NONE",
        "F8 = CLOSED",
        "#166 CAS-B1 = RESERVED / HARD HOLD",
        "READY deletion / lifecycle / GC / provider cleanup = CLOSED",
    ):
        assert phrase in document


def test_f7_t_eligibility_is_exact_committed_success_only():
    model = _read("se/src/infrastructure/storage/models/sql/agent/tool_result.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "commit_state IN ('PROVISIONAL', 'COMMITTED')",
        "success: Mapped[bool]",
        "error_code: Mapped[Optional[str]]",
        "error_message: Mapped[Optional[str]]",
        "retryable: Mapped[bool]",
        "output: Mapped[Optional[Any]]",
    ):
        assert phrase in model
    for phrase in (
        'result.commit_state == "COMMITTED"',
        "result.success is True",
        "result.error_code is None",
        "result.error_message is None",
        "result.retryable is False",
        "PROVISIONAL, failed, retryable, incomplete, cancelled, ambiguous, or OUTCOME_UNKNOWN execution state is not F7-T ingest authority",
    ):
        assert phrase in document


def test_f7_t_does_not_rewrite_committed_tool_result_or_ctx_payload():
    ctx = _semantic(_read(
        "docs/context_future/"
        "CTX_F5_3I_B1_TOOL_RESPONSE_PAYLOAD_SOURCE_AUTHORITY.md"
    ))
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    assert "Authoritative content is exactly AgentToolResultRecord.output." in ctx
    for phrase in (
        "MUST NOT rewrite, normalize, replace, or otherwise mutate AgentToolResultRecord.output",
        "authoritative CTX TOOL_RESPONSE_PAYLOAD source content remains exactly the durable committed AgentToolResultRecord.output",
        "rewrite AgentToolResultRecord.output to replace a media envelope with asset://...",
        "mutate CTX ToolResponsePayload source bytes or source identity",
        "future canonical asset is a separate CAS-derived publication keyed back to the immutable committed result",
    ):
        assert phrase in document


def test_f7_t_owner_and_provenance_are_durable_lineage_only():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "AgentToolResultRecord(result.id) -> AgentExecutionRecord(result.execution_id) -> canonical chat_data.Session(execution.session_id) -> Session.user_id",
        "result.invocation_id == canonical CapabilityInvocationRecord.id",
        "result.tool_call_id == canonical AgentToolCallRecord.tool_call_id",
        "result.execution_id == canonical execution/tool-call execution_id",
        "result.capability_id == canonical invocation/tool-call capability identity",
        "Caller payload owner fields, tool output owner fields, provider metadata, envelope metadata, and sandbox metadata have no owner authority",
    ):
        assert phrase in document


def test_arbitrary_media_looking_json_is_not_admitted():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "Arbitrary JSON strings, URLs, paths, base64-looking values, provider IDs, sandbox paths, filenames, MIME-looking fields, or nested objects are insufficient",
        "tool-owned typed media envelope",
        "media_ordinal",
        "bounded source descriptor",
        "Generic provider URL, arbitrary HTTP URL, local path, sandbox path, object key, signed URL, browser URL, provider file ID, and remote handle source classes remain CLOSED",
    ):
        assert phrase in document


def test_idempotency_and_duplicate_suppression_are_required_before_ingest():
    service = _read("se/src/application/assets/service.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    assert "def _asset_id()" in service
    assert 'return f"asset_{uuid.uuid4().hex}"' in service
    assert "async def ingest_stream(" in service
    for phrase in (
        "Current AssetService.ingest_stream() allocates new asset/blob IDs and is not itself idempotent",
        "F7TSourceKey = ( source_result_id, invocation_id, tool_call_id, capability_id, media_ordinal )",
        "durable READY projection already exists for F7TSourceKey -> return/reuse existing asset identity -> ZERO new ingest attempts",
        "another live canonicalization claim exists -> loser does not ingest",
        "prior outcome is ambiguous / projection state cannot prove SAFE retry -> ZERO automatic re-ingest",
        "Restart, recovery, duplicate event delivery, process crash, repeated scanner pass, and reprocessing MUST NOT silently create a second canonical asset",
    ):
        assert phrase in document


def test_asset_service_remains_only_storage_finalize_primitive():
    service = _read("se/src/application/assets/service.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "async def ingest_stream(",
        '"state": "STAGING"',
        'blob_record.state = "READY"',
        'values={"state": "READY"}',
        "_abort_staging_ingest(",
        'uri=f"asset://{file_record.id}"',
    ):
        assert phrase in service
    for phrase in (
        "AssetService.ingest_stream() remains the only current CAS storage/finalize primitive",
        "MUST NOT duplicate FileAsset/FileBlob SQL/ObjectStorage lifecycle logic",
        'FileAsset.origin_type = "TOOL"',
        "canonical durable media identity = asset://<asset_id>",
    ):
        assert phrase in document


def test_one_ingest_attempt_never_creates_tool_replay_authority():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "at most one authorized AssetService.ingest_stream(...) attempt after a durable exclusive claim is established",
        "process loss is not retry authority",
        "no tool replay, re-execution, provider retry/fallback, capability fallback, or UBQ re-admission may be used to regenerate bytes",
        "no second ingest may occur merely because the first caller did not observe its return value",
        "CAS failure is terminal to F7-T derivation and cannot be converted into permission to run the tool again",
    ):
        assert phrase in document


def test_validation_and_size_authority_are_frozen_before_first_ingest():
    settings = _read("se/src/infrastructure/config/schemas.py")
    service = _read("se/src/application/assets/service.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    assert "class AssetStorageSettings(BaseModel):" in settings
    assert "max_upload_bytes" in settings
    assert "observed_bytes > max_bytes" in service
    for phrase in (
        "Before the first CAS ingest attempt, deterministic validation must complete",
        "declared MIME is from an admitted media class",
        "filename is metadata only, sanitized, bounded, and cannot affect storage identity",
        "streamed observed bytes must be bounded by AssetStorageSettings.max_upload_bytes",
        "The server CAS ingest bound remains AssetStorageSettings.max_upload_bytes",
        "Client render-memory limits are not F7-T ingestion authority",
    ):
        assert phrase in document


def test_ctx_ubq_r12_and_sandbox_authority_do_not_transfer():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "F7-T occurs after committed tool outcome authority",
        "UBQ re-admission, recharge, settlement rewrite, refund, or budget mint",
        "lease/ResumeClaim/checkpoint/recovery mutation",
        "Issue #156 capability routing/sandbox remains separate",
        "WEB-DL-1 #165 remains RESERVED / NOT CLAIMED",
        "Issue #166 CAS-B1 remains RESERVED / HARD HOLD",
        "sandbox/provider-download -> CAS persistence",
        "Authority does not transfer by adjacency",
    ):
        assert phrase in document


def test_production_preclaim_exit_gate_is_explicit_and_finite():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "exact typed tool-media envelope/source classes",
        "exact post-COMMITTED trigger",
        "exact durable F7-T projection/reservation representation",
        "uniqueness/idempotency/concurrency behavior for F7TSourceKey",
        "exact origin_id/provenance encoding",
        "exact source byte acquisition authority",
        "MIME/filename/max-byte validation",
        "one-ingest-attempt/cancellation/process-loss behavior",
        "CTX opaque committed-output coexistence",
        "#156/#165/#166 non-overlap",
        "zero tool replay/fallback and zero UBQ authority expansion",
        "exact bounded production file list and regression matrix",
        "F7-T production implementation = CLOSED",
        "schema/migration authority = NONE",
        "runtime/wiring authority = NONE",
    ):
        assert phrase in document



def test_f7_t_p1_source_class_is_exact_inline_only_and_version_bound():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    invocation = _read(
        "se/src/infrastructure/storage/models/sql/capability/invocation.py"
    )
    capability_result = _read(
        "se/src/runtimes/capability/contracts/result.py"
    )
    tool_result = _read("se/src/runtimes/agent/contracts/tool.py")
    tools_v1 = _read("tools/v1/_shared/contracts.py")

    assert 'TOOL_RESULT_REQUIRED_KEYS = ("ok", "tool", "action", "data", "error", "meta")' in tools_v1
    assert "def tool_result_schema(data_schema:" in tools_v1

    for phrase in (
        "capability_version: Mapped[str | None]",
        "capability_id: Mapped[str]",
        "kind: Mapped[str]",
        "state: Mapped[str]",
    ):
        assert phrase in invocation
    assert "output: Any = None" in capability_result
    assert "class ToolExecutionResult(BaseModel):" in tool_result
    assert "output: Any = None" in tool_result

    for phrase in (
        "source_contract_id = F7T_INLINE_BASE64_V1",
        "source_transport = INLINE_BASE64_IN_COMMITTED_RESULT",
        "external retrieval = NONE",
        "URL/path/provider/sandbox dereference = FORBIDDEN",
        'contract": "F7T_INLINE_BASE64_V1"',
        'encoding": "base64"',
        "outer object must satisfy the canonical Tools V1 ToolResult success branch",
        "ok is exactly true",
        "error is exactly null",
        "meta.truncated is exactly false",
        'data keys are exactly {"$f7t_media"}',
        "A generic ToolResult with arbitrary data, a media-looking object outside data.$f7t_media, or a failed/truncated ToolResult remains ordinary opaque tool JSON and is not an F7-T source",
        'invocation.kind == "TOOL"',
        'invocation.state == "COMPLETED"',
        "invocation.capability_version is canonical non-empty",
        "MUST NOT determine source authority from the current mutable capability catalog",
        "(capability_id, capability_version) -> source_contract_id",
        "no wildcard capability ID",
        "no wildcard version",
        "unknown key = NOT ADMITTED / ZERO CAS mutation",
        "initial admitted capability/version entries = EMPTY",
        "active media ingest from tool results = ZERO until separate enrollment release",
    ):
        assert phrase in document


def test_f7_t_p1_reservation_projection_is_exact_unique_and_fail_closed():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    file_model = _read(
        "se/src/infrastructure/storage/models/sql/assets/file.py"
    )
    service = _read("se/src/application/assets/service.py")

    assert "origin_id: Mapped[Optional[str]]" in file_model
    assert "async def ingest_stream(" in service

    for phrase in (
        "ToolMediaAssetProjectionRecord",
        "table = cas_f7_t_tool_media_projections",
        "source_result_id FK agent_tool_results.id / RESTRICT / not null",
        "capability_version string / not null",
        "state RESERVED | INGESTING | READY | AMBIGUOUS",
        "asset_id FK files.id / RESTRICT / nullable",
        "UNIQUE( source_result_id, invocation_id, tool_call_id, capability_id, media_ordinal )",
        'projection.id = "f7tp_" + source_key_digest',
        'origin_id = "f7t:v1:" + source_key_digest',
        'origin_type="TOOL", origin_id=origin_id',
        "(no row) -- validated source + successful INSERT --> RESERVED",
        "RESERVED -- revision-CAS winner, durably committed before AssetService call --> INGESTING",
        "INGESTING -- AssetService returns READY asset + projection revision-CAS --> READY",
        "INGESTING -- local ingest call raises/cancels after call began --> AMBIGUOUS",
        "only the process that wins RESERVED -> INGESTING may call AssetService.ingest_stream()",
        "INGESTING is committed durably before the first ingest call",
        "observing pre-existing INGESTING after restart/process loss means ambiguous prior side effect and therefore ZERO automatic re-ingest",
        "AMBIGUOUS is terminal in this slice and therefore ZERO automatic re-ingest",
        "READY returns/reuses the stored asset_id with ZERO new ingest",
        "this slice prefers one possible orphan over a duplicate irreversible ingest",
    ):
        assert phrase in document


def test_f7_t_p1_future_persistence_scope_and_migration_lineage_are_finite():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    ubq2_migration = _read(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "26a_ubq2_dual_accounting_bridge.py"
    )

    assert 'revision: str = "26a_ubq2_dual_accounting_bridge"' in ubq2_migration

    for phrase in (
        "NEW se/src/infrastructure/storage/models/sql/assets/tool_media_projection.py",
        "MODIFY se/src/infrastructure/storage/models/sql/assets/__init__.py",
        "NEW se/src/infrastructure/storage/migrations/sql/versions/27a_cas_f7_t_tool_media_projection.py",
        "MODIFY se/src/infrastructure/storage/repositories/assets.py",
        "NEW se/src/application/assets/tool_generated_media.py",
        "NEW se/tests/application/assets/test_cas_f7_t_tool_generated_media.py",
        "revision = 27a_cas_f7_t_tool_media_projection",
        "down_revision = 26a_ubq2_dual_accounting_bridge",
        "Any new migration landing on canonical main before CLAIM is MATERIAL drift",
        "No se/src/runtimes/agent/, se/src/main.py, schema outside the new projection table, client code, #156 routing, or #166 bridge is included in this first persistence slice",
        "Because the initial capability enrollment registry is empty, this first slice can be tested without invoking CAS ingest from live Agent execution",
    ):
        assert phrase in document


def test_f7_t_p1_owner_amendment_does_not_self_approve_production():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "P1-CAS-F7T-SOURCE-CLASS-AUTHORITY-1: design ambiguity = ADDRESSED",
        "P1-CAS-F7T-DURABLE-RESERVATION-1: design ambiguity = ADDRESSED",
        "Only a fresh independent audit may mark either P1 CLOSED for production PRE-CLAIM",
        "This owner amendment does not self-approve production authority",
        "F7-T production implementation = CLOSED",
        "F7-T production CLAIM = NONE",
        "schema/migration authority = NONE",
        "runtime/wiring authority = NONE",
    ):
        assert phrase in document
