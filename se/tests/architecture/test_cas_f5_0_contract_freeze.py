from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, UniqueConstraint

from se.src.domain.schemas.attachment import GatewayAttachment
from se.src.infrastructure.config.schemas import ProviderConfig
from se.src.infrastructure.storage.models.sql.assets import (
    FileAssetRecord,
    FileProviderBindingRecord,
)
from se.src.runtimes.workflow.runtime import WorkflowRuntime


def test_f5_0_binding_foundation_is_implemented_without_provider_orchestration():
    table = FileProviderBindingRecord.__table__

    assert table.name == "file_provider_bindings"
    assert table.c.file_id.nullable is False
    assert table.c.provider_name.nullable is False
    assert table.c.provider_namespace.nullable is False

    # F5-1 foundation implements the schema contract while provider
    # orchestration and workflow hydration wiring remain separately closed.
    assert table.c.provider_file_id.nullable is True
    assert "blob_id" not in table.c
    assert "sha256" not in table.c
    assert "source_blob_id" in table.c
    assert "source_sha256" in table.c
    assert "live_claim_token" in table.c

    provider_identity_unique = [
        constraint
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_file_provider_bindings_provider_identity"
    ]
    assert len(provider_identity_unique) == 1
    assert {
        column.name for column in provider_identity_unique[0].columns
    } == {
        "provider_name",
        "provider_namespace",
        "provider_file_id",
    }

    state_constraints = [
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_file_provider_bindings_state"
    ]
    assert len(state_constraints) == 1
    for state in (
        "PROCESSING",
        "ACTIVE",
        "UNKNOWN",
        "EXPIRED",
        "DELETING",
        "DELETED",
        "ERROR",
    ):
        assert state in state_constraints[0]


@pytest.mark.parametrize(
    "extra",
    [
        {"provider_file_id": "files/provider-copy"},
        {"base64_data": "ZmFrZQ=="},
        {"bytes_data": b"raw"},
        {"uri": "https://provider.invalid/files/abc"},
        {"source": "provider"},
    ],
)
def test_f5_0_canonical_asset_shape_rejects_provider_or_byte_authority(extra):
    payload = {
        "asset_id": "asset-f5",
        "mime_type": "application/pdf",
        "source": "asset",
        **extra,
    }

    with pytest.raises((ValueError, ValidationError)):
        GatewayAttachment.model_validate(payload)


def test_f5_0_provider_identity_remains_secondary_to_file_asset():
    assert "provider_file_id" not in FileAssetRecord.__table__.c
    assert "provider_uri" not in FileAssetRecord.__table__.c


def test_f5_0_current_workflow_guard_remains_closed_before_implementation():
    source = textwrap.dedent(
        inspect.getsource(WorkflowRuntime._handle_context_built)
    )
    tree = ast.parse(source)

    asset_guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "contains_canonical_asset_content"
        in (ast.get_source_segment(source, node.test) or "")
    ]
    assert len(asset_guards) == 1

    guard = asset_guards[0]

    # F5-D-P2 is the separately released successor that narrows the
    # historical global gate. Canonical assets still enter the fail-closed
    # branch unless the positive DIRECT/AGENT activation gate is satisfied.
    test_source = ast.get_source_segment(source, guard.test) or ""
    assert isinstance(guard.test, ast.BoolOp)
    assert isinstance(guard.test.op, ast.And)
    assert "contains_canonical_asset_content" in test_source
    assert "_canonical_asset_dispatch_ready" in test_source

    guard_source = ast.get_source_segment(source, guard) or ""

    assert "ASSET_HYDRATION_REQUIRED" in guard_source
    assert '"failure_domain": "MESSAGE_ASSET"' in guard_source
    assert '"retryable": False' in guard_source
    assert guard.body
    assert isinstance(guard.body[-1], ast.Return)


def test_f5_0_binding_repository_surface_has_no_production_runtime_caller():
    root = Path("se/src")
    repository_path = Path(
        "se/src/infrastructure/storage/repositories/assets.py"
    ).as_posix()
    forbidden_calls = (
        "create_provider_binding(",
        "get_active_provider_binding(",
    )

    offenders: list[tuple[str, str]] = []
    for path in root.rglob("*.py"):
        normalized = path.as_posix()
        if normalized == repository_path:
            continue
        source = path.read_text(encoding="utf-8")
        for call in forbidden_calls:
            if call in source:
                offenders.append((normalized, call))

    assert offenders == []


def test_f5_0_provider_file_primitives_are_not_cas_application_authority():
    handler = Path("se/src/provider/handlers/file_handler.py").read_text(
        encoding="utf-8"
    )
    gemini_files = Path("se/src/provider/gemini/api/files.py").read_text(
        encoding="utf-8"
    )

    assert 'elif action == "upload"' in handler
    assert 'payload.get("file_bytes")' in handler
    assert "provider.files.upload_file(" in handler
    assert 'elif action == "delete"' in handler
    assert "provider.files.delete_file(" in handler

    assert "async def upload_file" in gemini_files
    assert "async def delete_file" in gemini_files

    # Provider primitives may exist, but CAS application authority must not
    # directly invoke remote provider upload/delete before F5 release.
    application_assets = Path("se/src/application/assets")
    offenders: list[tuple[str, str]] = []
    for path in application_assets.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for call in ("upload_file(", "delete_file("):
            if call in source:
                offenders.append((path.as_posix(), call))

    assert offenders == []



def test_f5_0_provider_namespace_authority_is_implemented_but_hydration_stays_closed():
    field = ProviderConfig.model_fields["file_binding_namespace"]
    assert field.default is None

    contract = Path(
        "docs/central_asset/CAS_F5_0_PROVIDER_HYDRATION_CONTRACT_200AA3DC.md"
    ).read_text(encoding="utf-8")

    required = (
        "ProviderConfig.file_binding_namespace: str | None = None",
        "HYDRATION_PROVIDER_SCOPE_UNCONFIGURED",
        'must not fall back to repository/schema default `"default"`',
        "different credential/account/project tenancies must use different namespaces",
    )
    for phrase in required:
        assert phrase in contract


def test_f5_0_normative_live_slot_authority_is_exact_and_db_enforced():
    contract = Path(
        "docs/central_asset/CAS_F5_0_PROVIDER_HYDRATION_CONTRACT_200AA3DC.md"
    ).read_text(encoding="utf-8")

    required = (
        "live_claim_token: str | None",
        'PROCESSING -> "LIVE"',
        'ACTIVE     -> "LIVE"',
        'UNKNOWN    -> "LIVE"',
        "UNIQUE(file_id, provider_name, provider_namespace, live_claim_token)",
        "COMMIT the replacement PROCESSING claim",
        "only after that commit may any remote upload mutation be attempted",
        "ACTIVE -> EXPIRED",
        "live_claim_token -> NULL",
        "COMMIT the ACTIVE -> EXPIRED transition",
        "if the ACTIVE -> EXPIRED CAS loses a race",
        "only after successful live-slot release",
        "If concurrent replacement/INSERT loses the unique constraint race",
    )
    for phrase in required:
        assert phrase in contract


def test_f5_0_remote_outcome_unknown_is_durable_and_not_retry_authority():
    contract = Path(
        "docs/central_asset/CAS_F5_0_PROVIDER_HYDRATION_CONTRACT_200AA3DC.md"
    ).read_text(encoding="utf-8")

    required = (
        "ProviderUploadOutcome.kind",
        "SAFE_NO_REMOTE_COMMIT",
        "REMOTE_SUCCESS_KNOWN",
        "REMOTE_OUTCOME_UNKNOWN",
        'state = "UNKNOWN"',
        'metadata_json["upload_outcome"] = "REMOTE_OUTCOME_UNKNOWN"',
        "no remote mutating upload/finalize request was sent",
        "do not automatically re-upload",
        "no automatic UNKNOWN recovery exists in the initial F5 slice",
        "stale PROCESSING is treated as UNKNOWN-equivalent",
    )
    for phrase in required:
        assert phrase in contract


def test_f5_0_content_fingerprint_release_contract_is_exact():
    contract = Path(
        "docs/central_asset/CAS_F5_0_PROVIDER_HYDRATION_CONTRACT_200AA3DC.md"
    ).read_text(encoding="utf-8")

    assert "source_blob_id" in contract
    assert "source_sha256" in contract
    assert (
        "ACTIVE reuse requires both to match the current READY FileBlob"
        in contract
    )



def test_f5_0_invalid_active_replacement_order_is_frozen():
    contract = Path(
        "docs/central_asset/CAS_F5_0_PROVIDER_HYDRATION_CONTRACT_200AA3DC.md"
    ).read_text(encoding="utf-8")

    required = (
        "ACTIVE -> validate current expiry and exact",
        "invalid/expired ACTIVE -> perform a revision/CAS-protected transition",
        "ACTIVE -> EXPIRED",
        "live_claim_token -> NULL",
        "COMMIT the ACTIVE -> EXPIRED transition before any replacement PROCESSING insert",
        "if the ACTIVE -> EXPIRED CAS loses a race",
        "re-read the current live winner and do not upload",
        "only after successful live-slot release may a new PROCESSING row be INSERTed",
        "concurrent replacement attempts cannot create two live claimants",
        "historical EXPIRED provider identity remains preserved",
        "does not delete the remote provider file",
    )
    for phrase in required:
        assert phrase in contract
