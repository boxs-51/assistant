from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F5_D_ACTIVATION_SURFACE_CONVERGENCE_CONTRACT_6228734A.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _normalize_ws(value: str) -> str:
    return " ".join(value.split())


def test_activation_contract_freezes_exact_zero_production_gate():
    contract = CONTRACT.read_text(encoding="utf-8")

    required = (
        "main@6228734ae7a380719bb14fa520e3307c5330aa31",
        "Architecture #1383 GREEN/GREEN",
        "PR #104 / CAS-F5-D-P1 LANDED / HEALTHY",
        "Production/runtime/schema/migration delta:** 0",
        "Production activation authority:** CLOSED",
        "The first later activation implementation may release **DIRECT + AGENT only**.",
        "legacy `provider.chat.execute` event | **CLOSED**",
        "session regeneration | **CLOSED**",
        "first production activation CLAIM MUST NOT broaden this matrix",
    )
    for phrase in required:
        assert phrase in contract


def test_provider_runtime_dependency_ownership_is_positive_and_future_compatible():
    provider = _read("se/src/runtimes/provider/runtime.py")
    kernel = _read("se/src/kernel/base.py")
    main = _read("se/src/main.py")

    assert "self._http_client = context.http_client" in provider
    assert "self.provider_registry = ProviderRegistry()" in provider
    assert "ProviderDiscovery(registry=self.provider_registry" in provider
    assert "self.chat_handler = ChatExecutionHandler(**handler_kwargs)" in provider

    assert "storage: Any" in kernel
    assert "uow_factory: Callable[[], Any]" in kernel
    assert "http_client: httpx.AsyncClient" in kernel

    assert "asset_storage_driver = config.assets.storage_driver" in main
    assert "storage_engine.is_driver_available(asset_storage_driver)" in main
    assert "storage_engine.get_object_storage_driver(asset_storage_driver)" in main

    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "use the exact `ProviderRuntime.provider_registry`",
        "CAS MUST NOT create a second ProviderRegistry",
        "use `RuntimeContext.uow_factory`",
        "`context.storage.get_object_storage_driver(...)`",
        "use the shared application `context.http_client`",
        "inject it into the exact production `ProviderRuntime.chat_handler` only",
        "CAS MUST NOT create a second logical-call budget",
    ):
        assert phrase in contract

    # The contract records the pre-activation baseline, but this architecture
    # evidence intentionally does not assert that F5-D hook symbols remain
    # absent from live production code forever. A separately released
    # activation stage is expected to add those symbols.
    assert "production ProviderRuntime bootstrap does not instantiate F5-C/F5-D services;" in contract


def test_activation_order_and_positive_readiness_rule_are_frozen():
    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))

    ordered = (
        "1. ProviderRuntime exact ProviderRegistry initialized",
        "2. configured CAS ObjectStorage driver proven available",
        "3. CanonicalAssetHydrationService constructed from:",
        "4. CanonicalAssetProviderProjectionHook constructed",
        "5. hook injected into the exact production ChatExecutionHandler",
        "6. server-owned runtime readiness proves the hook is installed/usable",
        "7. only then may a separately released implementation narrow WorkflowRuntime's asset guard",
    )
    cursor = -1
    for phrase in ordered:
        next_cursor = contract.index(phrase)
        assert next_cursor > cursor
        cursor = next_cursor

    for phrase in (
        "canonical asset request",
        "trusted authenticated owner is available from the released execution surface",
        "production F5-D hook is installed and server-readiness == ready",
        "execution surface is explicitly released",
        "fail closed before raw provider inference",
        "Readiness is server-owned runtime state.",
        "Request metadata, model metadata, attachment fields, session metadata, or client-supplied flags MUST NOT assert readiness or owner authority.",
        "Provider selection still occurs only in `ChatExecutionHandler`",
    ):
        assert phrase in contract


def test_workflow_guard_baseline_and_future_positive_replacement_are_frozen():
    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))

    # Freeze the historical/current baseline and the required replacement
    # semantics in the contract, without permanently asserting the old live
    # source shape. The next separately released activation stage is expected
    # to narrow this guard.
    for phrase in (
        "WorkflowRuntime still publishes `ASSET_HYDRATION_REQUIRED` / `MESSAGE_ASSET` / 409 before DIRECT, AGENT or legacy dispatch for canonical assets;",
        "The current global WorkflowRuntime asset guard MUST NOT be deleted.",
        "only then may a separately released implementation narrow WorkflowRuntime's asset guard",
        "hook unavailable/not-ready -> fail closed before raw provider inference",
        "canonical asset request",
        "production F5-D hook is installed and server-readiness == ready",
        "execution surface is explicitly released",
    ):
        assert phrase in contract


def test_direct_and_agent_keep_trusted_owner_handoff_to_shared_handler():
    direct = _read("se/src/runtimes/chat/direct.py")
    agent = _read("se/src/runtimes/agent/runtime.py")
    adapter = _read("se/src/runtimes/agent/adapters/inference.py")

    assert "owner_user_id=(" in direct
    assert "str(context.identity.user_id)" in agent
    assert 'provider_call_kwargs["owner_user_id"]' in adapter
    assert "handler.execute_with_fallback(" in adapter

    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))
    assert "DIRECT + AGENT only" in contract
    assert "passed separately from provider body/metadata" in contract


def test_legacy_provider_event_surface_is_normatively_closed_for_first_activation():
    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))

    # This slice freezes first-activation authority. It must not encode a
    # permanent live-source absence invariant that would prevent a later,
    # separately released trusted-identity handoff.
    for phrase in (
        "legacy `provider.chat.execute` event | **CLOSED**",
        "first activation implementation MUST keep asset-bearing generic `provider.chat.execute` execution closed",
        "request/client metadata MUST NOT become owner authority",
        "without trusted identity MUST fail closed before raw provider inference",
        "For any future legacy release:",
    ):
        assert phrase in contract


def test_session_regeneration_is_normatively_closed_for_first_activation():
    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))

    # Keep the first activation boundary frozen while remaining compatible
    # with a future separately audited regeneration release.
    for phrase in (
        "session regeneration | **CLOSED**",
        "first activation implementation MUST keep this asset-history guard CLOSED",
        "installing the production F5-D hook MUST NOT unlock regeneration",
        "A later regeneration release requires its own frozen trusted",
    ):
        assert phrase in contract


def test_landed_provider_attempt_safety_invariants_are_carried_forward():
    chat = _read("se/src/provider/handlers/chat_handler.py")
    projection = _read("se/src/application/assets/projection.py")

    assert "asset_projection_hook" in chat
    assert "asset_attempt_terminal" in chat
    assert "call_budget=call_budget" in chat
    assert "ProviderDeadlineExceededError" in chat
    assert "CanonicalAssetProviderProjectionHook" in projection
    assert "owner_user_id" in projection
    assert "_require_exact_selected_provider" in projection
    assert "provider_uri" in projection

    contract = _normalize_ws(CONTRACT.read_text(encoding="utf-8"))
    for phrase in (
        "no provider #2 hydration/projection after hook entry",
        "partial multi-asset UNKNOWN/failure remains terminal",
        "missing/blank Gemini provider URI fails closed",
        "foreign-owner or missing-owner asset hydration fails closed",
        "ProviderCallBudget deadline/cancellation remains one logical-call authority",
        "canonical request/history is not mutated",
        "provider-native identity is never persisted into canonical messages",
    ):
        assert phrase in contract


def test_activation_contract_freezes_required_evidence_and_closed_authorities():
    contract = CONTRACT.read_text(encoding="utf-8")

    evidence = (
        "DIRECT non-stream canonical asset flow",
        "DIRECT stream canonical asset flow",
        "AGENT non-stream canonical asset flow",
        "AGENT stream canonical asset flow",
        "wrapped `data.attachment` and flat `type=file / data=<GatewayAttachment>` forms",
        "trusted owner propagation and foreign-owner rejection",
        "missing Gemini `provider_uri/fileUri` fail-closed behavior",
        "partial multi-asset UNKNOWN/failure terminal behavior",
        "shared ProviderCallBudget deadline/cancellation behavior",
        "hook unavailable/not-ready -> fail closed before raw provider inference",
        "legacy provider event with no trusted identity -> fail closed for canonical assets",
        "session regeneration asset history remains blocked",
        "no provider #2 hydration after hook entry",
        "no provider-native identity persisted into canonical messages",
        "startup/readiness does not report F5-D ready",
    )
    for phrase in evidence:
        assert phrase in contract

    closed = (
        "production bootstrap / F5-D hook wiring",
        "WorkflowRuntime guard replacement or removal",
        "public end-to-end canonical asset activation",
        "asset-bearing legacy `provider.chat.execute`",
        "asset-bearing session regeneration",
        "automatic UNKNOWN / stale PROCESSING recovery",
        "provider remote delete / reclamation",
        "READY FileAsset deletion / release",
        "FileBlob / physical CAS GC / reconciliation",
        "CTX source-proof / Memory-promotion authority transfer",
        "R11/R12 authority expansion",
        "provider routing/fallback/deadline/model-selection ownership",
        "CAS-F6",
    )
    for phrase in closed:
        assert phrase in contract
