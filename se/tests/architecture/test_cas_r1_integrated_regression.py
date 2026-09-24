from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from se.src.application.assets.contracts import AssetDescriptor
from se.src.application.assets.service import AssetService
from se.src.application.messages.service import CanonicalMessageService
from se.src.context.source_adapters import project_asset_source
from se.src.context.source_identity import ContextSourceKind
from se.src.domain.schemas.attachment import GatewayAttachment
from se.src.domain.schemas.event import BaseEvent
from se.src.infrastructure.storage.models.sql.assets import (
    FileAssetRecord,
    FileBlobRecord,
    FileProviderBindingRecord,
    FileReferenceRecord,
)
from se.src.infrastructure.storage.repositories.chat_data.sessions import (
    SessionRepository,
)
from se.src.runtimes.workflow.runtime import WorkflowRuntime


def _ready_descriptor(*, revision: int = 7) -> AssetDescriptor:
    return AssetDescriptor(
        asset_id="asset-cas-r1",
        owner_user_id="user-cas-r1",
        filename="evidence.pdf",
        mime_type="application/pdf",
        size_bytes=123,
        sha256="a" * 64,
        state="READY",
        uri="asset://asset-cas-r1",
        origin_type="USER_UPLOAD",
        revision=revision,
    )


def test_cas_r1_identity_is_shared_without_transferring_lifecycle_authority():
    descriptor = _ready_descriptor(revision=7)

    ref = project_asset_source(descriptor)
    attachment = GatewayAttachment(
        asset_id=descriptor.asset_id,
        mime_type=descriptor.mime_type,
        source="asset",
    )

    assert ref.source_kind is ContextSourceKind.ASSET
    assert ref.authority_id == descriptor.asset_id
    assert ref.authority_version is None
    assert ref.owner_user_id == descriptor.owner_user_id
    assert ref.metadata["uri"] == descriptor.uri
    assert ref.metadata["file_asset_revision"] == 7

    assert attachment.asset_id == descriptor.asset_id
    assert attachment.uri == descriptor.uri
    assert attachment.provider_file_id is None
    assert attachment.base64_data is None
    assert attachment.bytes_data is None

    changed_revision = project_asset_source(_ready_descriptor(revision=8))
    assert changed_revision.context_source_id == ref.context_source_id


@pytest.mark.parametrize(
    "payload",
    [
        {
            "asset_id": "asset-cas-r1",
            "mime_type": "application/pdf",
            "source": "provider",
        },
        {
            "asset_id": "asset-cas-r1",
            "mime_type": "application/pdf",
            "source": "asset",
            "uri": "file:///tmp/evidence.pdf",
        },
        {
            "asset_id": "asset-cas-r1",
            "mime_type": "application/pdf",
            "source": "asset",
            "provider_file_id": "files/provider-copy",
        },
        {
            "asset_id": "asset-cas-r1",
            "mime_type": "application/pdf",
            "source": "asset",
            "base64_data": "ZmFrZQ==",
        },
    ],
)
def test_cas_r1_canonical_attachment_rejects_competing_identity(payload):
    with pytest.raises((ValueError, ValidationError)):
        GatewayAttachment.model_validate(payload)


def test_cas_r1_f1_model_and_migration_authority_remain_frozen():
    assert FileAssetRecord.__tablename__ == "files"
    assert FileBlobRecord.__tablename__ == "file_blobs"
    assert FileReferenceRecord.__tablename__ == "file_references"
    assert FileProviderBindingRecord.__tablename__ == "file_provider_bindings"

    assert "provider_file_id" not in FileAssetRecord.__table__.columns
    assert "provider_file_id" in FileProviderBindingRecord.__table__.columns

    migration = Path(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "18a_cas_r0_assets.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "18a_cas_r0_assets"' in migration
    assert (
        'down_revision: Union[str, None] = "17a_r11_checkpoint_cutover"'
        in migration
    )
    assert "Object-store I/O is forbidden" in migration
    assert "inside Alembic." in migration
    assert "ObjectStorageDriver" not in migration
    assert "object_store" not in migration


def test_cas_r1_delete_and_reference_release_boundaries_remain_fail_closed():
    delete_source = inspect.getsource(AssetService.delete_asset)
    assert "Agent release" in delete_source
    assert "authority can prove canonical readability may be revoked" in delete_source
    assert "raise AssetStateError" in delete_source
    assert "object_store.delete" not in delete_source

    session_delete_source = inspect.getsource(
        SessionRepository.delete_owned_session
    )
    assert "SessionAssetReferenceRetentionError" in session_delete_source
    assert "FileReferenceRecord.reference_type" in session_delete_source
    assert '"MESSAGE_CONTENT"' in session_delete_source
    assert '"SESSION_RESOURCE"' in session_delete_source

    message_source = inspect.getsource(
        CanonicalMessageService.persist_request_messages
    )
    reference_pos = message_source.index("create_message_references")
    commit_pos = message_source.rindex("await uow.commit()")
    assert reference_pos < commit_pos


def test_cas_r1_ctx_f2c_remains_projection_only():
    import se.src.context.source_adapters as adapters

    source = inspect.getsource(adapters)
    forbidden = (
        "sqlalchemy",
        "infrastructure.storage",
        "AssetService",
        "object_store",
        "FileAssetRecord",
        "FileBlobRecord",
        "FileReferenceRecord",
        "FileProviderBindingRecord",
    )
    for phrase in forbidden:
        assert phrase not in source


def test_cas_r1_r11_runtime_does_not_acquire_cas_physical_lifecycle_authority():
    root = Path("se/src/runtimes/agent")
    forbidden = (
        "FileAssetRecord",
        "FileBlobRecord",
        "FileReferenceRecord",
        "FileProviderBindingRecord",
        "ObjectStorageDriver",
    )
    offenders: list[tuple[str, str]] = []
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            if phrase in source:
                offenders.append((path.as_posix(), phrase))
    assert offenders == []


def test_cas_r1_f5_provider_binding_runtime_remains_inactive():
    root = Path("se/src")
    repository_path = (
        Path("se/src/infrastructure/storage/repositories/assets.py").as_posix()
    )
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


class _RecordingBus:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, event) -> None:
        self.published.append(event)


@pytest.mark.asyncio
async def test_cas_r1_pre_f5_guard_blocks_asset_history_before_provider_execution():
    runtime = WorkflowRuntime()
    runtime.event_bus = _RecordingBus()
    runtime.container = SimpleNamespace(
        direct_chat_runtime=object(),
        agent_runtime=object(),
    )
    calls = {"direct": 0, "agent": 0}

    async def direct(*args, **kwargs):
        calls["direct"] += 1

    async def agent(*args, **kwargs):
        calls["agent"] += 1

    runtime._execute_direct = direct
    runtime._execute_agent = agent

    await runtime._handle_context_built(
        BaseEvent(
            event_name="context.event.built",
            session_id="session-cas-r1",
            turn_id="turn-cas-r1",
            payload={
                "request_body": {
                    "_chat_execution_mode": "DIRECT",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "file",
                                    "data": {
                                        "attachment": {
                                            "asset_id": "asset-cas-r1",
                                            "source": "asset",
                                            "uri": "asset://asset-cas-r1",
                                        }
                                    },
                                }
                            ],
                        }
                    ],
                }
            },
        )
    )

    assert calls == {"direct": 0, "agent": 0}
    assert len(runtime.event_bus.published) == 1
    failure = runtime.event_bus.published[0]
    assert failure.event_name == "provider.failed"
    assert failure.payload["status_code"] == 409
    assert failure.payload["retryable"] is False
    assert failure.payload["failure_domain"] == "MESSAGE_ASSET"
    assert failure.payload["error_code"] == "ASSET_HYDRATION_REQUIRED"


def _ast_identifiers(tree: ast.AST) -> set[str]:
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.alias):
            identifiers.add(node.name)
            if node.asname is not None:
                identifiers.add(node.asname)
    return identifiers


def _string_literals(tree: ast.AST) -> tuple[str, ...]:
    return tuple(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _call_terminal_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def test_cas_r1_post_m2_ctx_f3_f4_remain_non_asset_consumers():
    paths = (
        Path("se/src/context/discovery.py"),
        Path("se/src/context/discovery_collection.py"),
        Path("se/src/context/access.py"),
    )
    forbidden_identifiers = {
        "ASSET",
        "AssetEvidence",
        "project_asset_source",
        "AssetService",
        "FileAssetRecord",
        "FileBlobRecord",
        "FileReferenceRecord",
        "FileProviderBindingRecord",
        "ObjectStorageDriver",
        "object_store",
    }
    forbidden_import_fragments = (
        "application.assets",
        "storage.models.sql.assets",
        "storage.repositories.assets",
    )

    offenders: list[tuple[str, str]] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path.as_posix())
        identifiers = _ast_identifiers(tree)
        literals = _string_literals(tree)

        for identifier in sorted(forbidden_identifiers & identifiers):
            offenders.append((path.as_posix(), f"identifier:{identifier}"))
        if "ASSET" in literals:
            offenders.append((path.as_posix(), "literal:ASSET"))
        if any(value.startswith("asset://") for value in literals):
            offenders.append((path.as_posix(), "literal:asset://"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports = (node.module or "",)
            else:
                continue
            for imported in imports:
                if any(fragment in imported for fragment in forbidden_import_fragments):
                    offenders.append((path.as_posix(), f"import:{imported}"))

    assert offenders == []


def test_cas_r1_post_m2_r11_migration_extends_cas_lineage_without_mutating_cas():
    path = Path(
        "se/src/infrastructure/storage/migrations/sql/versions/"
        "19a_r11_query_order_indexes.py"
    )
    migration = path.read_text(encoding="utf-8")
    tree = ast.parse(migration, filename=path.as_posix())

    assert 'revision: str = "19a_r11_query_order_indexes"' in migration
    assert 'down_revision: Union[str, None] = "18a_cas_r0_assets"' in migration

    actual_operations: list[tuple[str, str | None, str | None]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            continue

        operation = node.func.attr
        index_name = _literal_string(node.args[0]) if node.args else None
        if operation == "create_index":
            table_name = _literal_string(node.args[1]) if len(node.args) > 1 else None
        elif operation == "drop_index":
            table_name = next(
                (
                    _literal_string(keyword.value)
                    for keyword in node.keywords
                    if keyword.arg == "table_name"
                ),
                None,
            )
        else:
            table_name = None
        actual_operations.append((operation, index_name, table_name))

    assert sorted(actual_operations) == sorted(
        [
            (
                "create_index",
                "ix_agent_iterations_execution_iteration",
                "agent_iterations",
            ),
            (
                "create_index",
                "ix_agent_task_branches_task_created_branch",
                "agent_task_branches",
            ),
            (
                "drop_index",
                "ix_agent_task_branches_task_created_branch",
                "agent_task_branches",
            ),
            (
                "drop_index",
                "ix_agent_iterations_execution_iteration",
                "agent_iterations",
            ),
        ]
    )


def test_cas_r1_post_m2_r11_f1b_remains_agent_only_and_non_destructive():
    path = Path("se/src/runtimes/agent/gc_dry_run.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())

    forbidden_identifiers = {
        "FileAssetRecord",
        "FileBlobRecord",
        "FileReferenceRecord",
        "FileProviderBindingRecord",
        "ObjectStorageDriver",
        "object_store",
    }
    assert forbidden_identifiers.isdisjoint(_ast_identifiers(tree))
    assert not any(value.startswith("asset://") for value in _string_literals(tree))

    destructive_call_names = {
        "delete",
        "update",
        "insert",
        "flush",
        "commit",
        "merge",
        "add_all",
    }
    destructive_calls = sorted(
        {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for name in [_call_terminal_name(node)]
            if name in destructive_call_names
        }
    )
    assert destructive_calls == []

    destructive_sql = re.compile(
        r"\b(?:delete\s+from|insert\s+into|update\s+\S+\s+set|"
        r"truncate(?:\s+table)?|drop\s+table|alter\s+table)\b",
        re.IGNORECASE,
    )
    sql_literals = [
        value for value in _string_literals(tree) if destructive_sql.search(value)
    ]
    assert sql_literals == []
