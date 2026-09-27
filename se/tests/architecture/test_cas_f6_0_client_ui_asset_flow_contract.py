from __future__ import annotations

from pathlib import Path


CONTRACT = Path(
    "docs/central_asset/"
    "CAS_F6_0_CLIENT_UI_UNIFIED_ASSET_FLOW_CONTRACT_540900F9.md"
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _normalize(value: str) -> str:
    return " ".join(value.split())


def test_f6_0_contract_is_zero_production_and_baseline_locked():
    document = CONTRACT.read_text(encoding="utf-8")

    required = (
        "main@540900f950f4e7826a3a24f4a2e657c175ce818b",
        "production/runtime/schema/migration delta = ZERO",
        "CAS-F6 production implementation authority = CLOSED",
        "CAS-F7 = CLOSED",
        "CAS-F8 = CLOSED",
        "post-wave Architecture #1412 = GREEN/GREEN",
        "CAS-F5 initial provider-hydration milestone = COMPLETE",
    )
    for phrase in required:
        assert phrase in document


def test_existing_server_asset_surface_is_positive_landed_evidence():
    router = _read(
        "se/src/transport/gateway/api/v1/assets_router.py"
    )

    for phrase in (
        'APIRouter(prefix="/v1/assets"',
        '@router.post("", status_code=status.HTTP_201_CREATED)',
        '@router.get("")',
        '@router.get("/{asset_id}")',
        '@router.get("/{asset_id}/content")',
        "owner_user_id = _require_user_id(identity)",
        "owner_user_id=owner_user_id",
        "service.ingest_stream(",
        "service.list_assets(",
        "service.get_asset(",
        "service.open_content(",
        'Header(None, alias="Range")',
        "StreamingResponse(",
    ):
        assert phrase in router


def test_existing_canonical_attachment_shape_is_positive_landed_evidence():
    attachment = _read("cl/src/schemas/attachment.py")

    for phrase in (
        "asset_id: Optional[str]",
        '"asset"',
        'expected_uri = f"asset://{asset_id}"',
        "Canonical asset uri must match asset://<asset_id>.",
        'for field in ("base64_data", "bytes_data", "provider_file_id")',
        "Canonical asset attachment cannot carry",
        'data["source"] = "asset"',
        'data["uri"] = expected_uri',
    ):
        assert phrase in attachment


def test_f6_0_freezes_canonical_submission_and_descriptor_mapping():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "user-selected local file / bytes",
        "authenticated POST /v1/assets",
        "owner-authorized READY AssetDescriptor",
        'source="asset"',
        "uri = asset://<asset_id>",
        "ordinary DIRECT / AGENT chat request",
        "already-landed CAS-F5 hydration / provider projection",
        "provider_file_id = null",
        "base64_data = null",
        "bytes_data = null",
        "No client-supplied",
        "grants asset access.",
    ):
        assert phrase in document


def test_f6_0_freezes_partial_upload_fail_closed_semantics():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "SELECTED -> UPLOADING_CANONICAL -> READY_CANONICAL",
        "Upload failure is fail-closed for the canonical send path.",
        "MUST NOT automatically fall back",
        "/v1/files",
        "partial READY + partial FAILED",
        "MUST NOT silently auto-send a reduced subset",
        "may explicitly retry failed uploads",
        "may explicitly continue with the successfully persisted subset",
        "no implicit deletion/reclamation of already-created assets or provider bindings is allowed",
    ):
        assert phrase in document


def test_f6_0_freezes_authenticated_content_resolution_not_asset_uri_fetch():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "is a canonical identity URI only.",
        "authenticated GET /v1/assets/{asset_id}/content",
        "optional ephemeral in-browser object URL",
        "must not replace",
        "in canonical state",
        "must not be persisted in message/session history",
        "Range reads may be used for media/document UX",
    ):
        assert phrase in document


def test_f6_0_freezes_ui_normalization_identity():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "UI normalization must preserve",
        "asset_id",
        'source="asset"',
        'uri="asset://<asset_id>"',
        "Renderer-specific browser URLs are derived ephemerally",
        "are not written back into the normalized canonical attachment",
    ):
        assert phrase in document


def test_f6_0_freezes_legacy_f8_boundary_without_live_source_absence_trap():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "F6 may coexist with existing provider-specific",
        "Legacy cutover/removal belongs to CAS-F8.",
        "provider-generated response/media ingestion -> CAS-F7",
        "legacy attachment cutover/backfill/removal -> CAS-F8",
        "asset-bearing session regeneration release",
        "provider remote cleanup/reclamation/delete",
        "binding DELETING/DELETED production lifecycle",
        "orphan provider-file cleanup",
        "READY FileAsset deletion/release",
        "FileBlob/ObjectStorage physical GC/reconciliation",
        "automatic UNKNOWN/stale PROCESSING recovery",
        "CTX Memory promotion/source-proof/retrieval ownership",
        "R11 retention/destructive-GC ownership",
        "R12 lease/recovery/checkpoint ownership",
        "provider routing/fallback/deadline/model-selection ownership",
        "must not encode permanent live-source absence assertions",
    ):
        assert phrase in document


def test_f6_0_freezes_first_production_slice_matrix():
    document = CONTRACT.read_text(encoding="utf-8")

    expected_paths = (
        "cl/src/core/gateway_client.py",
        "cl/src/core/content_processor.py",
        "cl/src/ui/web/js/app.js",
        "cl/src/ui/web/js/components/inputFrame.js",
        "cl/src/ui/web/js/components/inputFrame/fileManager.js",
        "cl/src/ui/bridge.py",
        "cl/src/core/agent_engine.py",
        "cl/src/ui/web/js/components/console/normalizer.js",
        "cl/src/ui/web/js/components/console/blocks/fileBlock.js",
        "cl/src/ui/web/js/components/console/blocks/imageBlock.js",
        "cl/src/ui/web/js/components/console/blocks/mediaBlock.js",
        "cl/src/schemas/attachment.py",
        "cl/src/ui/web/js/components/console.js",
    )
    for path in expected_paths:
        assert path in document

    normalized = _normalize(document)
    for phrase in (
        "already-landed canonical server authority",
        "already-landed downstream execution",
        "storage schema/migrations",
        "outside first F6 slice",
        "CTX/R11/R12 ownership",
        "This is the candidate matrix for the first production CLAIM.",
        "It is not a production ownership grant.",
        "UIBridge.submit_prompt",
        "run_agent_session",
        "process_attached_files",
    ):
        assert phrase in normalized


def test_f6_0_does_not_claim_production_authority():
    document = _normalize(CONTRACT.read_text(encoding="utf-8"))

    for phrase in (
        "CAS-F6-0 = CONTRACT CANDIDATE",
        "CAS-F6 production CLAIM = CLOSED",
        "CAS-F7 = CLOSED",
        "CAS-F8 = CLOSED",
        "does not itself implement or authorize production F6 behavior.",
    ):
        assert phrase in document
