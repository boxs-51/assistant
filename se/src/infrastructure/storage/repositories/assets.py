from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.sql.assets import (
    FileAssetRecord,
    FileBlobRecord,
    FileProviderBindingRecord,
    FileReferenceRecord,
)


class AssetRepository:
    """Transaction-scoped persistence for F1 Central Asset Storage."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_blob(self, values: Mapping[str, Any]) -> FileBlobRecord:
        record = FileBlobRecord(**dict(values))
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_blob(self, blob_id: str) -> Optional[FileBlobRecord]:
        return await self.session.get(FileBlobRecord, blob_id)

    async def create_file(self, values: Mapping[str, Any]) -> FileAssetRecord:
        record = FileAssetRecord(**dict(values))
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_file(self, file_id: str) -> Optional[FileAssetRecord]:
        return await self.session.get(FileAssetRecord, file_id)

    async def list_files_by_owner(
        self,
        owner_user_id: str,
        *,
        states: Optional[Sequence[str]] = None,
        limit: int = 100,
    ) -> list[FileAssetRecord]:
        statement = (
            select(FileAssetRecord)
            .where(FileAssetRecord.owner_user_id == owner_user_id)
            .order_by(FileAssetRecord.created_at.desc())
            .limit(limit)
        )
        if states:
            statement = statement.where(FileAssetRecord.state.in_(tuple(states)))
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def create_reference(
        self, values: Mapping[str, Any]
    ) -> FileReferenceRecord:
        record = FileReferenceRecord(**dict(values))
        self.session.add(record)
        await self.session.flush()
        return record

    async def list_references_for_file(
        self, file_id: str
    ) -> list[FileReferenceRecord]:
        result = await self.session.execute(
            select(FileReferenceRecord)
            .where(FileReferenceRecord.file_id == file_id)
            .order_by(FileReferenceRecord.created_at.asc())
        )
        return list(result.scalars().all())

    async def create_provider_binding(
        self, values: Mapping[str, Any]
    ) -> FileProviderBindingRecord:
        record = FileProviderBindingRecord(**dict(values))
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_active_provider_binding(
        self,
        file_id: str,
        provider_name: str,
        *,
        provider_namespace: str = "default",
    ) -> Optional[FileProviderBindingRecord]:
        result = await self.session.execute(
            select(FileProviderBindingRecord)
            .where(
                FileProviderBindingRecord.file_id == file_id,
                FileProviderBindingRecord.provider_name == provider_name,
                FileProviderBindingRecord.provider_namespace
                == provider_namespace,
                FileProviderBindingRecord.state == "ACTIVE",
            )
            .order_by(FileProviderBindingRecord.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
