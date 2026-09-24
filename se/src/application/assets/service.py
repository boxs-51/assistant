from __future__ import annotations

import asyncio
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
    AssetTooLargeError,
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
        max_bytes: Optional[int] = None,
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
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if (
            max_bytes is not None
            and content_length is not None
            and content_length > max_bytes
        ):
            raise AssetTooLargeError(
                f"Upload is {content_length} bytes; limit is {max_bytes}."
            )

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

        observed_bytes = 0

        async def observed_stream():
            nonlocal observed_bytes
            async for chunk in stream:
                payload = bytes(chunk)
                observed_bytes += len(payload)
                if max_bytes is not None and observed_bytes > max_bytes:
                    raise AssetTooLargeError(
                        f"Upload exceeded {max_bytes} bytes."
                    )
                yield payload

        try:
            result = await self.object_store.put_stream(
                object_key,
                observed_stream(),
                content_length=content_length,
                content_type=mime_type,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._abort_staging_ingest(
                    asset_id,
                    blob_id,
                    object_key,
                )
            )
            raise
        except AssetTooLargeError:
            await self._abort_staging_ingest(
                asset_id,
                blob_id,
                object_key,
            )
            raise
        except Exception as exc:
            await self._abort_staging_ingest(
                asset_id,
                blob_id,
                object_key,
            )
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
        except asyncio.CancelledError:
            await asyncio.shield(
                self._abort_staging_ingest(
                    asset_id,
                    blob_id,
                    object_key,
                )
            )
            raise
        except Exception as exc:
            await self._abort_staging_ingest(
                asset_id,
                blob_id,
                object_key,
            )
            raise AssetFinalizeError(
                f"Object stored but SQL finalize failed for asset {asset_id}"
            ) from exc

    async def list_assets(
        self,
        *,
        owner_user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[AssetDescriptor, ...], int]:
        if not owner_user_id:
            raise ValueError("owner_user_id is required")
        if limit <= 0 or offset < 0:
            raise ValueError("Invalid asset pagination")
        async with self.uow_factory() as uow:
            rows = await uow.assets.list_ready_owned_file_rows(
                owner_user_id,
                limit=limit,
                offset=offset,
            )
            total = await uow.assets.count_ready_files_by_owner(
                owner_user_id
            )
            return (
                tuple(
                    self._descriptor(file_record, blob_record)
                    for file_record, blob_record in rows
                ),
                total,
            )

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
            if not allow_deleted and blob_record.state != "READY":
                raise AssetStateError(
                    f"Asset {asset_id} canonical blob is not READY"
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
        """Fail closed until Agent-owned release authority is available.

        Preserving object bytes is not enough: moving a live asset out of READY
        also revokes canonical readability. CAS-R0 therefore refuses the
        READY -> DELETING transition until R11-F can prove that retained Agent
        roots no longer require this asset.
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

            raise AssetStateError(
                f"Asset {asset_id} deletion is blocked until Agent release "
                "authority can prove canonical readability may be revoked"
            )

    @staticmethod
    def _authorize(file_record, owner_user_id: str) -> None:
        if file_record.owner_user_id != owner_user_id:
            raise AssetAccessDeniedError(file_record.id)

    async def _abort_staging_ingest(
        self,
        asset_id: str,
        blob_id: str,
        object_key: str,
    ) -> bool:
        """Abort only while this ingest still owns STAGING@0 authority.

        The FileAsset compare-and-set is the race fence. If READY publication
        already won, abort cleanup must not mutate the blob or delete bytes.
        """
        try:
            async with self.uow_factory() as uow:
                file_record = await uow.assets.get_file(asset_id)
                blob_record = await uow.assets.get_blob(blob_id)
                if (
                    file_record is None
                    or blob_record is None
                    or file_record.state != "STAGING"
                    or file_record.revision != 0
                    or blob_record.state != "STAGING"
                ):
                    return False

                winner = await uow.assets.compare_and_set_file(
                    asset_id,
                    expected_revision=0,
                    expected_state="STAGING",
                    values={"state": "ERROR"},
                )
                if winner is None:
                    return False

                blob_record.state = "ERROR"
                await uow.commit()
        except Exception:
            return False

        # Commit the durable ERROR fence first. If object cleanup fails or the
        # process exits here, the bytes remain attributable to this ERROR blob
        # and can never be mistaken for a READY canonical asset.
        try:
            await self.object_store.delete(object_key)
        except Exception:
            pass
        return True
