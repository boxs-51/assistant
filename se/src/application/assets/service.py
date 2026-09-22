from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterable, Optional
import uuid

from ...infrastructure.storage.interfaces.object import ObjectStorageDriver
from .contracts import AssetContent, AssetDescriptor
from .errors import (
    AssetAccessDeniedError,
    AssetFinalizeError,
    AssetNotFoundError,
    AssetStateError,
    AssetStorageError,
)


class AssetService:
    """Application authority for canonical user-owned file lifecycle."""

    def __init__(self, uow_factory, object_store: ObjectStorageDriver) -> None:
        self.uow_factory = uow_factory
        self.object_store = object_store

    @staticmethod
    def _asset_id() -> str:
        return f"asset_{uuid.uuid4().hex}"

    @staticmethod
    def _blob_id() -> str:
        return f"blob_{uuid.uuid4().hex}"

    @staticmethod
    def _object_key(blob_id: str) -> str:
        return f"blobs/{blob_id}"

    @staticmethod
    def _descriptor(file_record, blob_record) -> AssetDescriptor:
        return AssetDescriptor(
            asset_id=file_record.id,
            owner_user_id=file_record.owner_user_id,
            filename=file_record.filename,
            mime_type=file_record.mime_type,
            size_bytes=blob_record.size_bytes,
            sha256=blob_record.sha256,
            state=file_record.state,
            uri=f"asset://{file_record.id}",
            origin_type=file_record.origin_type,
            revision=file_record.revision,
        )

    async def ingest_stream(
        self,
        *,
        owner_user_id: str,
        filename: str,
        mime_type: str,
        stream: AsyncIterable[bytes],
        content_length: Optional[int] = None,
        organization_id: Optional[str] = None,
        origin_type: str = "USER_UPLOAD",
        origin_id: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> AssetDescriptor:
        if not owner_user_id:
            raise ValueError("owner_user_id is required")
        if not filename:
            raise ValueError("filename is required")
        if not mime_type:
            raise ValueError("mime_type is required")

        asset_id = self._asset_id()
        blob_id = self._blob_id()
        object_key = self._object_key(blob_id)

        async with self.uow_factory() as uow:
            await uow.assets.create_blob(
                {
                    "id": blob_id,
                    "storage_backend": getattr(
                        self.object_store,
                        "backend_name",
                        self.object_store.__class__.__name__,
                    ),
                    "bucket": None,
                    "object_key": object_key,
                    "state": "STAGING",
                    "metadata_json": {},
                }
            )
            await uow.assets.create_file(
                {
                    "id": asset_id,
                    "owner_user_id": owner_user_id,
                    "organization_id": organization_id,
                    "blob_id": blob_id,
                    "filename": filename,
                    "mime_type": mime_type,
                    "origin_type": origin_type,
                    "origin_id": origin_id,
                    "state": "STAGING",
                    "revision": 0,
                    "metadata_json": dict(metadata or {}),
                }
            )
            await uow.commit()

        try:
            result = await self.object_store.put_stream(
                object_key,
                stream,
                content_length=content_length,
                content_type=mime_type,
            )
        except Exception as exc:
            await self._best_effort_mark_error(asset_id, blob_id)
            raise AssetStorageError(
                f"Failed to persist object for asset {asset_id}"
            ) from exc

        try:
            async with self.uow_factory() as uow:
                file_record = await uow.assets.get_file(asset_id)
                blob_record = await uow.assets.get_blob(blob_id)
                if file_record is None or blob_record is None:
                    raise AssetFinalizeError(
                        "Asset/blob disappeared before finalize"
                    )
                if file_record.state != "STAGING" or file_record.revision != 0:
                    raise AssetStateError(
                        f"Asset {asset_id} is not STAGING@0"
                    )
                if blob_record.state != "STAGING":
                    raise AssetStateError(
                        f"Blob {blob_id} is not STAGING"
                    )

                blob_record.state = "READY"
                blob_record.size_bytes = result.size_bytes
                blob_record.sha256 = result.sha256
                blob_record.detected_mime_type = mime_type
                blob_record.etag = result.etag
                blob_record.verified_at = datetime.now(timezone.utc)

                winner = await uow.assets.compare_and_set_file(
                    asset_id,
                    expected_revision=0,
                    expected_state="STAGING",
                    values={"state": "READY"},
                )
                if winner is None:
                    raise AssetStateError(
                        f"Asset {asset_id} finalize lost its CAS"
                    )
                await uow.commit()
                return self._descriptor(winner, blob_record)
        except Exception as exc:
            raise AssetFinalizeError(
                f"Object stored but SQL finalize failed for asset {asset_id}"
            ) from exc

    async def get_asset(
        self,
        *,
        owner_user_id: str,
        asset_id: str,
        allow_deleted: bool = False,
    ) -> AssetDescriptor:
        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            if file_record is None:
                raise AssetNotFoundError(asset_id)
            self._authorize(file_record, owner_user_id)
            if not allow_deleted and file_record.state != "READY":
                raise AssetStateError(
                    f"Asset {asset_id} is not READY"
                )
            blob_record = await uow.assets.get_blob(file_record.blob_id)
            if blob_record is None:
                raise AssetStateError(
                    f"Asset {asset_id} has no blob"
                )
            return self._descriptor(file_record, blob_record)

    async def open_content(
        self,
        *,
        owner_user_id: str,
        asset_id: str,
        byte_range: Optional[tuple[int, Optional[int]]] = None,
    ) -> AssetContent:
        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            if file_record is None:
                raise AssetNotFoundError(asset_id)
            self._authorize(file_record, owner_user_id)
            if file_record.state != "READY":
                raise AssetStateError(
                    f"Asset {asset_id} is not READY"
                )
            blob_record = await uow.assets.get_blob(file_record.blob_id)
            if blob_record is None or blob_record.state != "READY":
                raise AssetStateError(
                    f"Asset {asset_id} canonical blob is not READY"
                )
            descriptor = self._descriptor(file_record, blob_record)
            object_key = blob_record.object_key

        stream = await self.object_store.open_stream(
            object_key,
            byte_range=byte_range,
        )
        return AssetContent(descriptor=descriptor, stream=stream)

    async def delete_asset(
        self,
        *,
        owner_user_id: str,
        asset_id: str,
    ) -> AssetDescriptor:
        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            if file_record is None:
                raise AssetNotFoundError(asset_id)
            self._authorize(file_record, owner_user_id)
            if file_record.state == "DELETED":
                blob_record = await uow.assets.get_blob(file_record.blob_id)
                if blob_record is None:
                    raise AssetStateError(
                        f"Deleted asset {asset_id} has no tombstone blob"
                    )
                return self._descriptor(file_record, blob_record)
            if file_record.state != "READY":
                raise AssetStateError(
                    f"Asset {asset_id} cannot be deleted from "
                    f"state {file_record.state}"
                )
            blob_record = await uow.assets.get_blob(file_record.blob_id)
            if blob_record is None:
                raise AssetStateError(
                    f"Asset {asset_id} has no blob"
                )
            expected_revision = file_record.revision
            winner = await uow.assets.compare_and_set_file(
                asset_id,
                expected_revision=expected_revision,
                expected_state="READY",
                values={"state": "DELETING"},
            )
            if winner is None:
                raise AssetStateError(
                    f"Asset {asset_id} delete lost its CAS"
                )
            blob_record.state = "DELETING"
            object_key = blob_record.object_key
            blob_id = blob_record.id
            deleting_revision = winner.revision
            await uow.commit()

        try:
            await self.object_store.delete(object_key)
        except Exception as exc:
            raise AssetStorageError(
                f"Failed to delete object for asset {asset_id}"
            ) from exc

        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            blob_record = await uow.assets.get_blob(blob_id)
            if file_record is None or blob_record is None:
                raise AssetFinalizeError(
                    f"Asset {asset_id} disappeared during delete finalize"
                )
            winner = await uow.assets.compare_and_set_file(
                asset_id,
                expected_revision=deleting_revision,
                expected_state="DELETING",
                values={
                    "state": "DELETED",
                    "deleted_at": datetime.now(timezone.utc),
                },
            )
            if winner is None:
                raise AssetStateError(
                    f"Asset {asset_id} delete finalize lost its CAS"
                )
            blob_record.state = "DELETED"
            blob_record.deleted_at = datetime.now(timezone.utc)
            await uow.commit()
            return self._descriptor(winner, blob_record)

    @staticmethod
    def _authorize(file_record, owner_user_id: str) -> None:
        if file_record.owner_user_id != owner_user_id:
            raise AssetAccessDeniedError(file_record.id)

    async def _best_effort_mark_error(
        self,
        asset_id: str,
        blob_id: str,
    ) -> None:
        try:
            async with self.uow_factory() as uow:
                file_record = await uow.assets.get_file(asset_id)
                blob_record = await uow.assets.get_blob(blob_id)
                if file_record is not None and file_record.state == "STAGING":
                    await uow.assets.compare_and_set_file(
                        asset_id,
                        expected_revision=file_record.revision,
                        expected_state="STAGING",
                        values={"state": "ERROR"},
                    )
                if blob_record is not None and blob_record.state == "STAGING":
                    blob_record.state = "ERROR"
                await uow.commit()
        except Exception:
            return
