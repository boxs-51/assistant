from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterable, Optional
import uuid

from ...infrastructure.storage.interfaces.object import ObjectStorageDriver
from .contracts import AssetContent, AssetDescriptor
from .errors import (
    AssetAccessDeniedError,
    AssetFinalizeError,
    AssetInUseError,
    AssetMimeMismatchError,
    AssetNotFoundError,
    AssetStateError,
    AssetStorageError,
)
from .mime import (
    canonical_mime_type,
    detect_mime_type,
    mime_types_compatible,
    normalize_mime_type,
)


_MIME_SAMPLE_LIMIT = 8192


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
            declared_mime_type=blob_record.declared_mime_type,
            detected_mime_type=blob_record.detected_mime_type,
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

        declared_mime = normalize_mime_type(mime_type)
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
                    "declared_mime_type": declared_mime,
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
                    "mime_type": declared_mime,
                    "origin_type": origin_type,
                    "origin_id": origin_id,
                    "state": "STAGING",
                    "revision": 0,
                    "metadata_json": dict(metadata or {}),
                }
            )
            await uow.commit()

        sample = bytearray()

        async def observed_stream():
            async for chunk in stream:
                payload = bytes(chunk)
                if len(sample) < _MIME_SAMPLE_LIMIT:
                    remaining = _MIME_SAMPLE_LIMIT - len(sample)
                    sample.extend(payload[:remaining])
                yield payload

        try:
            result = await self.object_store.put_stream(
                object_key,
                observed_stream(),
                content_length=content_length,
                content_type=declared_mime,
            )
        except Exception as exc:
            await self._best_effort_mark_error(asset_id, blob_id)
            raise AssetStorageError(
                f"Failed to persist object for asset {asset_id}"
            ) from exc

        detected_mime = detect_mime_type(bytes(sample))
        if not mime_types_compatible(declared_mime, detected_mime):
            try:
                await self.object_store.delete(object_key)
            except Exception:
                # Reconciliation can collect the orphan later. The semantic
                # failure remains a MIME mismatch rather than an SDK error.
                pass
            await self._best_effort_mark_error(
                asset_id,
                blob_id,
                detected_mime_type=detected_mime,
            )
            raise AssetMimeMismatchError(
                f"Declared MIME {declared_mime!r} conflicts with "
                f"detected MIME {detected_mime!r}."
            )

        canonical_mime = canonical_mime_type(
            declared_mime,
            detected_mime,
        )
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
                blob_record.declared_mime_type = declared_mime
                blob_record.detected_mime_type = detected_mime
                blob_record.etag = result.etag
                blob_record.verified_at = datetime.now(timezone.utc)

                winner = await uow.assets.compare_and_set_file(
                    asset_id,
                    expected_revision=0,
                    expected_state="STAGING",
                    values={
                        "state": "READY",
                        "mime_type": canonical_mime,
                    },
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
        """Request logical deletion without deleting canonical bytes inline.

        Physical GC is intentionally separated so an R7 WAITING/RUNNING
        execution cannot lose bytes between checkpoint reconstruction and
        provider hydration.
        """
        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            if file_record is None:
                raise AssetNotFoundError(asset_id)
            self._authorize(file_record, owner_user_id)
            blob_record = await uow.assets.get_blob(file_record.blob_id)
            if blob_record is None:
                raise AssetStateError(
                    f"Asset {asset_id} has no blob"
                )
            if file_record.state in {"DELETING", "DELETED"}:
                return self._descriptor(file_record, blob_record)
            if file_record.state != "READY":
                raise AssetStateError(
                    f"Asset {asset_id} cannot be deleted from "
                    f"state {file_record.state}"
                )
            if await uow.assets.has_live_references(asset_id):
                raise AssetInUseError(
                    f"Asset {asset_id} is pinned by an active R7 execution."
                )

            winner = await uow.assets.compare_and_set_file(
                asset_id,
                expected_revision=file_record.revision,
                expected_state="READY",
                values={"state": "DELETING"},
            )
            if winner is None:
                raise AssetStateError(
                    f"Asset {asset_id} delete lost its CAS"
                )
            blob_winner = await uow.assets.compare_and_set_blob_state(
                blob_record.id,
                expected_state="READY",
                values={"state": "DELETING"},
            )
            if blob_winner is None:
                await uow.rollback()
                raise AssetStateError(
                    f"Asset {asset_id} blob is not READY for deletion."
                )
            await uow.commit()
            return self._descriptor(winner, blob_winner)

    async def collect_deleting_asset(
        self,
        asset_id: str,
    ) -> AssetDescriptor:
        """Physically collect one already-fenced DELETING asset."""
        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            if file_record is None:
                raise AssetNotFoundError(asset_id)
            blob_record = await uow.assets.get_blob(file_record.blob_id)
            if blob_record is None:
                raise AssetStateError(
                    f"Asset {asset_id} has no blob"
                )
            if file_record.state == "DELETED":
                return self._descriptor(file_record, blob_record)
            if file_record.state != "DELETING":
                raise AssetStateError(
                    f"Asset {asset_id} is not DELETING"
                )
            if await uow.assets.has_live_references(asset_id):
                raise AssetInUseError(
                    f"Asset {asset_id} regained a live R7 reference."
                )
            object_key = blob_record.object_key
            blob_id = blob_record.id
            expected_revision = file_record.revision

        try:
            await self.object_store.delete(object_key)
        except Exception as exc:
            raise AssetStorageError(
                f"Failed to collect object for asset {asset_id}"
            ) from exc

        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            blob_record = await uow.assets.get_blob(blob_id)
            if file_record is None or blob_record is None:
                raise AssetFinalizeError(
                    f"Asset {asset_id} disappeared during GC finalize"
                )
            winner = await uow.assets.compare_and_set_file(
                asset_id,
                expected_revision=expected_revision,
                expected_state="DELETING",
                values={
                    "state": "DELETED",
                    "deleted_at": datetime.now(timezone.utc),
                },
            )
            if winner is None:
                raise AssetStateError(
                    f"Asset {asset_id} GC finalize lost its CAS"
                )
            blob_winner = await uow.assets.compare_and_set_blob_state(
                blob_id,
                expected_state="DELETING",
                values={
                    "state": "DELETED",
                    "deleted_at": datetime.now(timezone.utc),
                },
            )
            if blob_winner is None:
                await uow.rollback()
                raise AssetStateError(
                    f"Asset {asset_id} blob GC finalize lost its CAS"
                )
            await uow.commit()
            return self._descriptor(winner, blob_winner)

    async def reconcile_asset(
        self,
        asset_id: str,
        *,
        reclaim_staging: bool = False,
        cleanup_error_object: bool = True,
    ) -> AssetDescriptor:
        """Reconcile one known asset after a partial SQL/object-store failure."""
        async with self.uow_factory() as uow:
            file_record = await uow.assets.get_file(asset_id)
            if file_record is None:
                raise AssetNotFoundError(asset_id)
            blob_record = await uow.assets.get_blob(file_record.blob_id)
            if blob_record is None:
                raise AssetStateError(
                    f"Asset {asset_id} has no blob"
                )
            descriptor = self._descriptor(file_record, blob_record)
            state = file_record.state
            object_key = blob_record.object_key
            blob_id = blob_record.id
            revision = file_record.revision

        if state == "DELETING":
            return await self.collect_deleting_asset(asset_id)

        if state == "READY":
            if await self.object_store.exists(object_key):
                return descriptor
            async with self.uow_factory() as uow:
                winner = await uow.assets.compare_and_set_file(
                    asset_id,
                    expected_revision=revision,
                    expected_state="READY",
                    values={"state": "ERROR"},
                )
                blob_winner = await uow.assets.compare_and_set_blob_state(
                    blob_id,
                    expected_state="READY",
                    values={"state": "MISSING"},
                )
                if winner is None or blob_winner is None:
                    await uow.rollback()
                    raise AssetStateError(
                        f"Asset {asset_id} missing-blob reconciliation lost its CAS"
                    )
                await uow.commit()
                return self._descriptor(winner, blob_winner)

        if state == "STAGING" and reclaim_staging:
            if await self.object_store.exists(object_key):
                await self.object_store.delete(object_key)
            async with self.uow_factory() as uow:
                winner = await uow.assets.compare_and_set_file(
                    asset_id,
                    expected_revision=revision,
                    expected_state="STAGING",
                    values={"state": "ERROR"},
                )
                blob_winner = await uow.assets.compare_and_set_blob_state(
                    blob_id,
                    expected_state="STAGING",
                    values={"state": "ERROR"},
                )
                if winner is None or blob_winner is None:
                    await uow.rollback()
                    raise AssetStateError(
                        f"Asset {asset_id} staging reconciliation lost its CAS"
                    )
                await uow.commit()
                return self._descriptor(winner, blob_winner)

        if state == "ERROR" and cleanup_error_object:
            if await self.object_store.exists(object_key):
                await self.object_store.delete(object_key)
            return descriptor

        return descriptor

    @staticmethod
    def _authorize(file_record, owner_user_id: str) -> None:
        if file_record.owner_user_id != owner_user_id:
            raise AssetAccessDeniedError(file_record.id)

    async def _best_effort_mark_error(
        self,
        asset_id: str,
        blob_id: str,
        *,
        detected_mime_type: Optional[str] = None,
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
                    values = {"state": "ERROR"}
                    if detected_mime_type is not None:
                        values["detected_mime_type"] = detected_mime_type
                    await uow.assets.compare_and_set_blob_state(
                        blob_id,
                        expected_state="STAGING",
                        values=values,
                    )
                await uow.commit()
        except Exception:
            return
