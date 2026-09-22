from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.application.assets import (
    AssetAccessDeniedError,
    AssetService,
    AssetStateError,
    AssetStorageError,
)
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.object_local.driver import (
    LocalObjectStorageDriver,
)
from se.src.infrastructure.storage.models.sql.base import Base
from se.src.infrastructure.storage.repositories.assets import AssetRepository


class _Uow:
    def __init__(self, sessions):
        self._sessions = sessions
        self._ctx = None

    async def __aenter__(self):
        self._ctx = self._sessions()
        self.session = await self._ctx.__aenter__()
        self.assets = AssetRepository(self.session)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type is not None:
                await self.session.rollback()
        finally:
            await self._ctx.__aexit__(exc_type, exc, tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


async def _chunks(payload: bytes, split: int = 3):
    for start in range(0, len(payload), split):
        yield payload[start:start + split]


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return engine, sessions


@pytest.mark.asyncio
async def test_f2_asset_service_ingest_authorize_range_and_deferred_delete(tmp_path):
    engine, sessions = await _database()
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    service = AssetService(lambda: _Uow(sessions), driver)
    payload = b"central asset bytes"
    try:
        created = await service.ingest_stream(
            owner_user_id="user-a",
            filename="note.txt",
            mime_type="text/plain",
            stream=_chunks(payload),
            content_length=len(payload),
        )
        assert created.state == "READY"
        assert created.uri == f"asset://{created.asset_id}"
        assert created.size_bytes == len(payload)
        assert created.sha256 == hashlib.sha256(payload).hexdigest()
        assert created.declared_mime_type == "text/plain"
        assert created.detected_mime_type == "text/plain"
        assert created.revision == 1

        loaded = await service.get_asset(
            owner_user_id="user-a",
            asset_id=created.asset_id,
        )
        assert loaded == created

        with pytest.raises(AssetAccessDeniedError):
            await service.get_asset(
                owner_user_id="user-b",
                asset_id=created.asset_id,
            )

        content = await service.open_content(
            owner_user_id="user-a",
            asset_id=created.asset_id,
            byte_range=(8, 12),
        )
        assert b"".join([chunk async for chunk in content.stream]) == payload[8:13]

        deleting = await service.delete_asset(
            owner_user_id="user-a",
            asset_id=created.asset_id,
        )
        assert deleting.state == "DELETING"
        assert deleting.revision == 2
        async with _Uow(sessions) as uow:
            file_record = await uow.assets.get_file(created.asset_id)
            blob = await uow.assets.get_blob(file_record.blob_id)
            assert await driver.exists(blob.object_key) is True

        deleted = await service.collect_deleting_asset(created.asset_id)
        assert deleted.state == "DELETED"
        assert deleted.revision == 3

        with pytest.raises(AssetStateError):
            await service.get_asset(
                owner_user_id="user-a",
                asset_id=created.asset_id,
            )
    finally:
        await driver.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_f2_file_state_cas_rejects_stale_revision():
    engine, sessions = await _database()
    try:
        async with _Uow(sessions) as uow:
            blob = await uow.assets.create_blob(
                {
                    "id": "blob-cas",
                    "storage_backend": "object-local",
                    "object_key": "blobs/blob-cas",
                    "state": "STAGING",
                }
            )
            await uow.assets.create_file(
                {
                    "id": "asset-cas",
                    "owner_user_id": "user-a",
                    "blob_id": blob.id,
                    "filename": "x.bin",
                    "mime_type": "application/octet-stream",
                    "origin_type": "USER_UPLOAD",
                    "state": "STAGING",
                    "revision": 0,
                }
            )
            winner = await uow.assets.compare_and_set_file(
                "asset-cas",
                expected_revision=0,
                expected_state="STAGING",
                values={"state": "ERROR"},
            )
            assert winner is not None
            assert winner.revision == 1

            stale = await uow.assets.compare_and_set_file(
                "asset-cas",
                expected_revision=0,
                expected_state="STAGING",
                values={"state": "READY"},
            )
            assert stale is None
            await uow.commit()
    finally:
        await engine.dispose()


class _FailingStore:
    backend_name = "failing"

    async def put_stream(self, *args, **kwargs):
        raise OSError("simulated object failure")


@pytest.mark.asyncio
async def test_f2_failed_object_write_marks_staging_asset_error():
    engine, sessions = await _database()
    service = AssetService(lambda: _Uow(sessions), _FailingStore())
    try:
        with pytest.raises(AssetStorageError):
            await service.ingest_stream(
                owner_user_id="user-a",
                filename="broken.bin",
                mime_type="application/octet-stream",
                stream=_chunks(b"boom"),
                content_length=4,
            )

        async with _Uow(sessions) as uow:
            files = await uow.assets.list_files_by_owner(
                "user-a",
                states=["ERROR"],
            )
            assert len(files) == 1
            blob = await uow.assets.get_blob(files[0].blob_id)
            assert blob is not None
            assert blob.state == "ERROR"
            assert files[0].revision == 1
    finally:
        await engine.dispose()
