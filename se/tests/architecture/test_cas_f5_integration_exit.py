from __future__ import annotations

from pathlib import Path


EXIT = Path(
    "docs/central_asset/CAS_F5_INTEGRATION_EXIT_CB24DFE9.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _normalize(value: str) -> str:
    return " ".join(value.split())


def test_f5_exit_is_zero_production_and_baseline_locked():
    document = EXIT.read_text(encoding="utf-8")

    required = (
        "main@cb24dfe92696c7ca25108f1b024a68803c5a9f41",
        "Production/runtime/schema/migration delta: 0",
        "PR #110 frozen HEAD = 5fef0dc36b0e2cc50e2fee15a648a0c2c6ab30c5",
        "post-merge Architecture #1399 = GREEN/GREEN",
        "CAS-F5-D-P2 = LANDED / CANONICAL / HEALTHY",
        "CAS-F6 authority: CLOSED",
    )
    for phrase in required:
        assert phrase in document


def test_f5_exit_references_canonical_contract_chain():
    document = EXIT.read_text(encoding="utf-8")

    for path in (
        "docs/central_asset/CAS_F5_0_PROVIDER_HYDRATION_CONTRACT_200AA3DC.md",
        "docs/central_asset/CAS_F5_D_PROVIDER_PINNED_TRANSIENT_PROJECTION_CONTRACT_912CF1AC.md",
        "docs/central_asset/CAS_F5_D_ACTIVATION_SURFACE_CONVERGENCE_CONTRACT_6228734A.md",
    ):
        assert Path(path).is_file()
        assert path in document


def test_f5_binding_foundation_positive_evidence_is_landed():
    model = _read(
        "se/src/infrastructure/storage/models/sql/assets/provider_binding.py"
    )
    migration = _read(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "20a_cas_f5_binding_foundation.py"
    )

    for phrase in (
        "source_blob_id",
        "source_sha256",
        "live_claim_token",
        "uq_file_provider_bindings_live_slot",
        "'UNKNOWN'",
        "ck_file_provider_bindings_active_provider_identity",
        "ck_file_provider_bindings_live_claim",
    ):
        assert phrase in model

    for phrase in (
        'revision: str = "20a_cas_f5_binding_foundation"',
        "provider_file_id",
        "source_blob_id",
        "source_sha256",
        "live_claim_token",
        "uq_file_provider_bindings_live_slot",
        "'UNKNOWN'",
    ):
        assert phrase in migration


def test_f5_hydration_and_projection_positive_evidence_is_landed():
    hydration = _read("se/src/application/assets/hydration.py")
    projection = _read("se/src/application/assets/projection.py")
    chat = _read("se/src/provider/handlers/chat_handler.py")

    for phrase in (
        "class CanonicalAssetHydrationService",
        "HYDRATION_OUTCOME_UNKNOWN",
        "HYDRATION_FINGERPRINT_DRIFT",
        "HYDRATION_PERSISTENCE_CONFLICT",
        "owner_user_id",
        "provider_name",
    ):
        assert phrase in hydration

    assert "class CanonicalAssetProviderProjectionHook" in projection
    assert "_require_exact_selected_provider" in projection
    assert "TRANSIENT_PROVIDER_ASSET_PROJECTION_KEY" in projection
    assert "asset_attempt_terminal" in chat
    assert "call_budget=call_budget" in chat


def test_f5_direct_agent_activation_positive_evidence_is_landed():
    provider = _read("se/src/runtimes/provider/runtime.py")
    workflow = _read("se/src/runtimes/workflow/runtime.py")
    direct = _read("se/src/runtimes/chat/direct.py")
    agent = _read("se/src/runtimes/agent/runtime.py")
    adapter = _read("se/src/runtimes/agent/adapters/inference.py")

    for phrase in (
        "CanonicalAssetHydrationService",
        "CanonicalAssetProviderProjectionHook",
        "def asset_projection_ready",
        "self.chat_handler.asset_projection_hook = asset_projection_hook",
    ):
        assert phrase in provider

    assert 'mode not in {"DIRECT", "AGENT"}' in workflow
    assert "_canonical_asset_dispatch_ready" in workflow
    assert "owner_user_id=(" in direct
    assert "str(context.identity.user_id)" in agent
    assert 'provider_call_kwargs["owner_user_id"]' in adapter


def test_f5_exit_preserves_closed_surfaces_normatively_without_future_trap():
    document = _normalize(EXIT.read_text(encoding="utf-8"))

    closed = (
        "asset-bearing legacy provider.chat.execute",
        "asset-bearing session regeneration",
        "automatic UNKNOWN reconciliation/recovery",
        "automatic stale-PROCESSING recovery",
        "provider remote file delete/reclamation/cleanup",
        "READY FileAsset deletion/release",
        "FileBlob / physical CAS GC or reconciliation",
        "CTX Memory promotion/source-proof authority",
        "R11 retention/deletion authority expansion",
        "R12 Agent lease/recovery/checkpoint authority",
        "provider routing/fallback/deadline/model-selection ownership",
        "CAS-F6",
    )
    for phrase in closed:
        assert phrase in document

    assert (
        "does not encode permanent live-source absence assertions"
        in document
    )


def test_f5_exit_does_not_claim_f6_or_closed_surface_completion():
    document = _normalize(EXIT.read_text(encoding="utf-8"))

    for phrase in (
        "CAS-F5 initial provider-hydration milestone = EXIT CANDIDATE",
        "CAS-F6 = CLOSED",
        "closed lifecycle/recovery/destructive surfaces = STILL CLOSED",
        "CAS-F6 roadmap transition = ELIGIBLE FOR SEPARATE RELEASE",
        "The exit does not itself CLAIM or implement CAS-F6.",
    ):
        assert phrase in document
