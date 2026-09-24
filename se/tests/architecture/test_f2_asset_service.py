from __future__ import annotations

import asyncio
import hashlib

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import se.src.infrastructure.storage.core.unit_of_work  # noqa: F401
from se.src.application.assets import (
    AssetAccessDeniedError,
    AssetService,
    AssetStateError,
    AssetStorageError,
    AssetTooLargeError,
)
from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.object_local.driver import (
    LocalObjectStorageDriver,
)
from se.src.infrastructure.storage.interfaces.object import ObjectWriteResult
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
async def test_f2_asset_service_delete_fails_closed_and_preserves_readability(tmp_path):
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

        with pytest.raises(AssetStateError, match="release authority"):
            await service.delete_asset(
                owner_user_id="user-a",
                asset_id=created.asset_id,
            )

        async with _Uow(sessions) as uow:
            file_record = await uow.assets.get_file(created.asset_id)
            blob_record = await uow.assets.get_blob(file_record.blob_id)
            assert file_record.state == "READY"
            assert file_record.revision == 1
            assert blob_record.state == "READY"
            assert await driver.exists(blob_record.object_key) is True

        after_rejected_delete = await service.get_asset(
            owner_user_id="user-a",
            asset_id=created.asset_id,
        )
        assert after_rejected_delete.state == "READY"
        assert after_rejected_delete.revision == 1

        still_readable = await service.open_content(
            owner_user_id="user-a",
            asset_id=created.asset_id,
        )
        assert b"".join(
            [chunk async for chunk in still_readable.stream]
        ) == payload
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

async def _cancel_during_stream():
    yield b"partial"
    raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_f3_cancel_during_stream_aborts_staging_and_propagates(tmp_path):
    engine, sessions = await _database()
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    service = AssetService(lambda: _Uow(sessions), driver)
    try:
        with pytest.raises(asyncio.CancelledError):
            await service.ingest_stream(
                owner_user_id="user-a",
                filename="cancelled.bin",
                mime_type="application/octet-stream",
                stream=_cancel_during_stream(),
            )

        async with _Uow(sessions) as uow:
            files = await uow.assets.list_files_by_owner(
                "user-a",
                states=["ERROR"],
            )
            assert len(files) == 1
            file_record = files[0]
            assert file_record.revision == 1
            blob = await uow.assets.get_blob(file_record.blob_id)
            assert blob is not None
            assert blob.state == "ERROR"
            assert await driver.exists(blob.object_key) is False
    finally:
        await driver.disconnect()
        await engine.dispose()


class _CancelAfterCanonicalWriteStore:
    backend_name = "cancel-after-write"

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.delete_calls: list[str] = []

    async def put_stream(
        self,
        object_key,
        stream,
        *,
        content_length=None,
        content_type=None,
    ):
        del content_type
        payload = b"".join([chunk async for chunk in stream])
        if content_length is not None:
            assert len(payload) == content_length
        self.objects[object_key] = payload
        raise asyncio.CancelledError()

    async def delete(self, object_key):
        self.delete_calls.append(object_key)
        self.objects.pop(object_key, None)


@pytest.mark.asyncio
async def test_f3_cancel_after_object_write_removes_bytes_and_aborts_staging():
    engine, sessions = await _database()
    store = _CancelAfterCanonicalWriteStore()
    service = AssetService(lambda: _Uow(sessions), store)
    try:
        with pytest.raises(asyncio.CancelledError):
            await service.ingest_stream(
                owner_user_id="user-a",
                filename="cancel-after-write.bin",
                mime_type="application/octet-stream",
                stream=_chunks(b"written-before-cancel"),
                content_length=len(b"written-before-cancel"),
            )

        async with _Uow(sessions) as uow:
            files = await uow.assets.list_files_by_owner(
                "user-a",
                states=["ERROR"],
            )
            assert len(files) == 1
            file_record = files[0]
            assert file_record.revision == 1
            blob = await uow.assets.get_blob(file_record.blob_id)
            assert blob is not None
            assert blob.state == "ERROR"
            assert blob.object_key in store.delete_calls
            assert blob.object_key not in store.objects
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_f3_abort_fence_cannot_delete_ready_asset_bytes(tmp_path):
    engine, sessions = await _database()
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    service = AssetService(lambda: _Uow(sessions), driver)
    payload = b"ready-wins"
    try:
        created = await service.ingest_stream(
            owner_user_id="user-a",
            filename="ready.bin",
            mime_type="application/octet-stream",
            stream=_chunks(payload),
            content_length=len(payload),
        )

        async with _Uow(sessions) as uow:
            file_record = await uow.assets.get_file(created.asset_id)
            blob = await uow.assets.get_blob(file_record.blob_id)
            blob_id = blob.id
            object_key = blob.object_key

        aborted = await service._abort_staging_ingest(
            created.asset_id,
            blob_id,
            object_key,
        )
        assert aborted is False

        async with _Uow(sessions) as uow:
            file_record = await uow.assets.get_file(created.asset_id)
            blob = await uow.assets.get_blob(blob_id)
            assert file_record.state == "READY"
            assert file_record.revision == 1
            assert blob.state == "READY"
            assert await driver.exists(object_key) is True

        content = await service.open_content(
            owner_user_id="user-a",
            asset_id=created.asset_id,
        )
        assert b"".join([chunk async for chunk in content.stream]) == payload
    finally:
        await driver.disconnect()
        await engine.dispose()

@pytest.mark.asyncio
async def test_f3_streamed_byte_limit_aborts_to_error_without_object(tmp_path):
    engine, sessions = await _database()
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    service = AssetService(lambda: _Uow(sessions), driver)
    try:
        with pytest.raises(AssetTooLargeError):
            await service.ingest_stream(
                owner_user_id="user-a",
                filename="too-large.bin",
                mime_type="application/octet-stream",
                stream=_chunks(b"123456789", split=2),
                content_length=None,
                max_bytes=8,
            )

        async with _Uow(sessions) as uow:
            files = await uow.assets.list_files_by_owner(
                "user-a",
                states=["ERROR"],
            )
            assert len(files) == 1
            assert files[0].revision == 1
            blob = await uow.assets.get_blob(files[0].blob_id)
            assert blob is not None
            assert blob.state == "ERROR"
            assert await driver.exists(blob.object_key) is False
    finally:
        await driver.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_f3_declared_byte_limit_rejects_before_staging(tmp_path):
    engine, sessions = await _database()
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    service = AssetService(lambda: _Uow(sessions), driver)
    try:
        with pytest.raises(AssetTooLargeError):
            await service.ingest_stream(
                owner_user_id="user-a",
                filename="declared-too-large.bin",
                mime_type="application/octet-stream",
                stream=_chunks(b"1234"),
                content_length=4,
                max_bytes=3,
            )

        async with _Uow(sessions) as uow:
            assert await uow.assets.list_files_by_owner("user-a") == []
    finally:
        await driver.disconnect()
        await engine.dispose()


@pytest.mark.asyncio
async def test_f3_list_assets_is_owner_scoped_and_ready_blob_only(tmp_path):
    engine, sessions = await _database()
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    service = AssetService(lambda: _Uow(sessions), driver)
    try:
        ready_a = await service.ingest_stream(
            owner_user_id="user-a",
            filename="a.bin",
            mime_type="application/octet-stream",
            stream=_chunks(b"a"),
            content_length=1,
        )
        await service.ingest_stream(
            owner_user_id="user-b",
            filename="b.bin",
            mime_type="application/octet-stream",
            stream=_chunks(b"b"),
            content_length=1,
        )
        async with _Uow(sessions) as uow:
            error_blob = await uow.assets.create_blob(
                {
                    "id": "blob-error-f3",
                    "storage_backend": "object-local",
                    "object_key": "blobs/blob-error-f3",
                    "state": "ERROR",
                }
            )
            await uow.assets.create_file(
                {
                    "id": "asset-error-f3",
                    "owner_user_id": "user-a",
                    "blob_id": error_blob.id,
                    "filename": "error.bin",
                    "mime_type": "application/octet-stream",
                    "origin_type": "USER_UPLOAD",
                    "state": "ERROR",
                    "revision": 1,
                }
            )
            await uow.commit()

        items, total = await service.list_assets(
            owner_user_id="user-a",
            limit=10,
            offset=0,
        )
        assert total == 1
        assert [item.asset_id for item in items] == [ready_a.asset_id]
        assert all(item.state == "READY" for item in items)
    finally:
        await driver.disconnect()
        await engine.dispose()

