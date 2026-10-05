from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F7_T_ACTIVATION_ENROLLMENT_BOUNDARY_D967165A.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _semantic(value: str) -> str:
    return " ".join(value.replace("**", "").replace(chr(96), "").split())


def test_a0_is_exact_zero_production_contract_candidate():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "CAS-F7-T-A0",
        "main@d967165a5c8cda6ffaf9a172f14b9dc0696bf140",
        "Architecture: #2092 / 37224467909 / GREEN-GREEN",
        "mode = CONTRACT + ARCHITECTURE EVIDENCE ONLY",
        "production/runtime/schema/migration delta = ZERO",
        "production CLAIM = NONE",
        "production activation PRE-CLAIM = HOLD",
        "concrete capability enrollment = PROHIBITED",
        "merge authority = NONE",
        "#166 CAS-B1 = RESERVED / HARD HOLD / NO CLAIM",
        "F8 = CLOSED",
        "READY deletion / lifecycle / GC / provider cleanup / reconciliation = CLOSED",
    ):
        assert phrase in document


def test_current_f7t_persistence_is_dormant_and_empty_enrollment():
    source = _read("se/src/application/assets/tool_generated_media.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        'F7T_INLINE_BASE64_V1 = "F7T_INLINE_BASE64_V1"',
        "EMPTY_F7T_ENROLLMENT: Mapping[tuple[str, str], str] = MappingProxyType({})",
        "class ToolGeneratedMediaCanonicalizer:",
        "async def canonicalize_committed_result(",
        "if size_bytes > max_media_bytes:",
    ):
        assert phrase in source

    assert "max_media_items" not in source

    for phrase in (
        "production enrollment registry = EMPTY",
        "live Agent F7-T trigger/wiring = ABSENT",
        "the default enrollment is EMPTY_F7T_ENROLLMENT",
        "no production caller invokes canonicalize_committed_result(...)",
    ):
        assert phrase in document


def test_no_production_tool_or_capability_emits_f7t_v1_on_a0_baseline():
    allowed = {
        Path("se/src/application/assets/tool_generated_media.py"),
    }
    observed: set[Path] = set()

    for root in (Path("se/src"), Path("tools/v1")):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "F7T_INLINE_BASE64_V1" in text or "$f7t_media" in text:
                observed.add(path)

    assert observed == allowed

    document = _semantic(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "first producer tuple = UNRESOLVED",
        "producer owner handoff = ABSENT",
        "concrete enrollment entry = NONE",
        "P1-CAS-F7T-ACT-PRODUCER-1 = GAP / HOLD",
        "The owner MUST NOT invent a tuple merely to make A1 claimable",
    ):
        assert phrase in document


def test_agent_runtime_has_commit_then_reload_seam_but_no_f7t_caller():
    runtime = _read("se/src/runtimes/agent/runtime.py")
    persistence = _read("se/src/runtimes/agent/persistence.py")
    main = _read("se/src/main.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    assert "await self._persist_tool_result(result, iteration_id)" in runtime
    assert "committed_result = await self._load_committed_tool_result(" in runtime
    assert 'values["commit_state"] = "COMMITTED"' in persistence
    assert "ToolGeneratedMediaCanonicalizer" not in runtime
    assert "canonicalize_committed_result" not in runtime
    assert "ToolGeneratedMediaCanonicalizer" not in main

    for phrase in (
        "post-COMMITTED Agent caller seam is frozen below but #107 authority is not transferred",
        "only after AgentRuntime has obtained an exact durable COMMITTED AgentToolResultRecord",
        "before DurableAgentStore.save_tool_result(...)",
        "inside the SQL transaction that commits AgentToolResultRecord",
        "on a transport-only ToolExecutionResult that has not been re-read as COMMITTED",
    ):
        assert phrase in document


def test_a0_freezes_finite_first_activation_bounds():
    config = _read("se/src/infrastructure/config/schemas.py")
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    assert "class AssetStorageSettings(BaseModel):" in config
    assert "max_upload_bytes: int = Field(default=268_435_456, gt=0)" in config

    for phrase in (
        "max_media_items = 8",
        "per_item_decoded_bytes <= config.assets.max_upload_bytes",
        "aggregate_decoded_bytes <= config.assets.max_upload_bytes",
        "admitted media_kind = image",
        "image/png",
        "image/jpeg",
        "image/webp",
        "1 <= len(items) <= 8",
        "the sum of decoded payload lengths across all items must not exceed the same server-owned config.assets.max_upload_bytes ceiling",
        "no sniffed MIME, extension-derived MIME, wildcard image/* or producer override is accepted",
    ):
        assert phrase in document


def test_a0_freezes_duplicate_and_failure_isolation_without_retry_authority():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "READY projection -> reuse asset identity / ZERO new ingest",
        "RESERVED observed by a non-winner -> ZERO ingest",
        "pre-existing INGESTING -> ZERO automatic re-ingest",
        "AMBIGUOUS -> ZERO automatic re-ingest",
        "no resume/recovery path may reset projection state",
        "no Agent retry/replay path may be invoked because F7-T was rejected, failed, cancelled, ambiguous or unavailable",
        "AgentToolResultRecord.output = immutable",
        "AgentToolResultRecord.commit_state = COMMITTED",
        "tool retryability = unchanged",
        "Agent continuation authority = unchanged",
        "CTX TOOL_RESPONSE_PAYLOAD source = unchanged",
        "UBQ admission/settlement/refund = unchanged",
        "convert a successful committed tool result into an Agent failure",
        "trigger tool replay/re-execution/fallback",
    ):
        assert phrase in document


def test_a0_freezes_exact_future_a1_six_path_maximum():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "MODIFY se/src/application/assets/tool_generated_media.py",
        "MODIFY se/src/runtimes/agent/runtime.py",
        "MODIFY se/src/main.py",
        "MODIFY se/tests/application/assets/test_cas_f7_t_tool_generated_media.py",
        "MODIFY se/tests/architecture/test_cas_f7_t_activation_enrollment_boundary.py",
        "NEW se/tests/integration/test_cas_f7_t_live_activation.py",
        "No ApplicationContainer field is required",
        "Any need for a seventh path is a PRE-CLAIM invalidation",
    ):
        assert phrase in document


def test_a0_preserves_agent_producer_and_sandbox_ownership_boundaries():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "Issue #74 owns: - CAS enrollment admission",
        "Issue #107 / Agent owns: - tool execution outcome truth",
        "A1 cannot modify se/src/runtimes/agent/runtime.py until a fresh bilateral audit explicitly confirms",
        "CAS does not own implementation of the first producer",
        "A1's six-path maximum intentionally contains no producer implementation file",
        "A0 and future A1 remain inline-only",
        "#165 WEB-DL-1",
        "#166 sandbox/provider-download -> CAS bridge",
        "#166 is not the next production CAS slice",
    ):
        assert phrase in document


def test_a0_keeps_destructive_and_reconciliation_authority_closed():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "READY FileAsset deletion/release",
        "FileBlob deletion",
        "physical GC",
        "orphan cleanup",
        "AMBIGUOUS reconciliation",
        "INGESTING repair",
        "provider remote cleanup",
        "projection deletion",
        "CAS-F8",
        "Repair/reconciliation remains a separate future stage",
    ):
        assert phrase in document


def test_a1_remains_not_claimable_until_all_release_gates_pass():
    document = _semantic(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "A1 production PRE-CLAIM may PASS only when all are true",
        "exact first producer capability_id + capability_version is named",
        "producer owner handoff proves intentional exact F7T_INLINE_BASE64_V1 output",
        "max_media_items=8 is preserved",
        "aggregate decoded bytes <= config.assets.max_upload_bytes is preserved",
        "exact post-COMMITTED AgentRuntime sidecar is independently accepted by #107",
        "the call occurs outside the Agent result commit transaction",
        "exact six-path maximum remains sufficient",
        "blocking P0/P1 = NONE",
        "CAS-F7-T-A1 = HOLD / NOT CLAIMABLE",
        "concrete enrollment = EMPTY",
        "live Agent activation = CLOSED",
    ):
        assert phrase in document
