from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.infrastructure.storage.models.sql.assets import (
    FileAssetRecord,
    FileBlobRecord,
    FileProviderBindingRecord,
    FileReferenceRecord,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.assets import AssetRepository


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


@pytest.mark.asyncio
async def test_f1_asset_repository_round_trips_stable_file_and_secondary_binding():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            assets = AssetRepository(session)
            blob = await assets.create_blob(
                {
                    "id": "blob-f1",
                    "storage_backend": "local",
                    "bucket": None,
                    "object_key": "users/u1/blob-f1",
                    "state": "READY",
                    "size_bytes": 4,
                    "sha256": "a" * 64,
                    "detected_mime_type": "text/plain",
                }
            )
            file = await assets.create_file(
                {
                    "id": "asset-f1",
                    "owner_user_id": "u1",
                    "blob_id": blob.id,
                    "filename": "note.txt",
                    "mime_type": "text/plain",
                    "extension": "txt",
                    "origin_type": "USER_UPLOAD",
                    "state": "READY",
                }
            )
            reference = await assets.create_reference(
                {
                    "id": "ref-f1",
                    "file_id": file.id,
                    "reference_type": "SESSION_RESOURCE",
                    "session_id": "session-f1",
                }
            )
            binding = await assets.create_provider_binding(
                {
                    "id": "binding-f1",
                    "file_id": file.id,
                    "provider_name": "gemini",
                    "provider_namespace": "default",
                    "provider_file_id": "files/provider-f1",
                    "provider_uri": "https://provider.invalid/file",
                    "state": "ACTIVE",
                }
            )
            await session.commit()

        async with sessions() as session:
            assets = AssetRepository(session)
            stored = await assets.get_file("asset-f1")
            listed = await assets.list_files_by_owner("u1", states=["READY"])
            references = await assets.list_references_for_file("asset-f1")
            active = await assets.get_active_provider_binding(
                "asset-f1", "gemini"
            )
            assert stored is not None
            assert stored.blob_id == "blob-f1"
            assert [item.id for item in listed] == ["asset-f1"]
            assert [item.id for item in references] == [reference.id]
            assert active is not None
            assert active.id == binding.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f1_ready_blob_requires_size_and_sha256():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            session.add(
                FileBlobRecord(
                    id="bad-ready-blob",
                    storage_backend="local",
                    object_key="bad",
                    state="READY",
                    size_bytes=None,
                    sha256=None,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f1_reference_requires_exactly_one_locator_shape():
    engine, sessions = await _database()
    try:
        async with sessions() as session:
            session.add(
                FileBlobRecord(
                    id="blob-ref",
                    storage_backend="local",
                    object_key="blob-ref",
                    state="READY",
                    size_bytes=1,
                    sha256="b" * 64,
                )
            )
            session.add(
                FileAssetRecord(
                    id="asset-ref",
                    owner_user_id="u1",
                    blob_id="blob-ref",
                    filename="x.bin",
                    mime_type="application/octet-stream",
                    origin_type="USER_UPLOAD",
                    state="READY",
                )
            )
            await session.flush()
            session.add(
                FileReferenceRecord(
                    id="bad-ref",
                    file_id="asset-ref",
                    reference_type="MESSAGE_CONTENT",
                    message_id="message-f1",
                    session_id="session-f1",
                    content_part_index=0,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
    finally:
        await engine.dispose()


def test_f1_models_keep_provider_identity_secondary_to_asset():
    assert FileAssetRecord.__tablename__ == "files"
    assert FileBlobRecord.__tablename__ == "file_blobs"
    assert FileReferenceRecord.__tablename__ == "file_references"
    assert FileProviderBindingRecord.__tablename__ == "file_provider_bindings"
    assert "provider_file_id" not in FileAssetRecord.__table__.columns
    assert "provider_file_id" in FileProviderBindingRecord.__table__.columns
