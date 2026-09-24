from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.sql.chat_data.session import Message
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

    async def list_ready_owned_file_rows(
        self,
        owner_user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[FileAssetRecord, FileBlobRecord]]:
        statement = (
            select(FileAssetRecord, FileBlobRecord)
            .join(
                FileBlobRecord,
                FileAssetRecord.blob_id == FileBlobRecord.id,
            )
            .where(
                FileAssetRecord.owner_user_id == owner_user_id,
                FileAssetRecord.state == "READY",
                FileBlobRecord.state == "READY",
            )
            .order_by(
                FileAssetRecord.created_at.desc(),
                FileAssetRecord.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return list(result.all())

    async def count_ready_files_by_owner(
        self,
        owner_user_id: str,
    ) -> int:
        statement = (
            select(func.count(FileAssetRecord.id))
            .join(
                FileBlobRecord,
                FileAssetRecord.blob_id == FileBlobRecord.id,
            )
            .where(
                FileAssetRecord.owner_user_id == owner_user_id,
                FileAssetRecord.state == "READY",
                FileBlobRecord.state == "READY",
            )
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    async def _insert_reference(
        self, values: Mapping[str, Any]
    ) -> FileReferenceRecord:
        record = FileReferenceRecord(**dict(values))
        self.session.add(record)
        await self.session.flush()
        return record

    async def create_reference(
        self, values: Mapping[str, Any]
    ) -> FileReferenceRecord:
        if values.get("reference_type") == "MESSAGE_CONTENT":
            raise ValueError(
                "Use create_message_references() for MESSAGE_CONTENT."
            )
        return await self._insert_reference(values)

    async def get_owned_ready_file(
        self,
        *,
        file_id: str,
        owner_user_id: str,
    ) -> tuple[FileAssetRecord, FileBlobRecord]:
        file_record = await self.get_file(file_id)
        if file_record is None:
            raise KeyError(f"Unknown asset: {file_id}")
        if file_record.owner_user_id != owner_user_id:
            raise PermissionError(f"Asset {file_id} is not owned by this user.")
        if file_record.state != "READY":
            raise ValueError(f"Asset {file_id} is not READY.")
        blob_record = await self.get_blob(file_record.blob_id)
        if blob_record is None or blob_record.state != "READY":
            raise ValueError(f"Asset {file_id} canonical blob is not READY.")
        return file_record, blob_record

    async def pin_owned_ready_file(
        self,
        *,
        file_id: str,
        owner_user_id: str,
    ) -> tuple[FileAssetRecord, FileBlobRecord]:
        # Reference additions are commutative. Pin with one owner/state-scoped
        # UPDATE so concurrent READY pins serialize instead of false-conflicting
        # on a stale lifecycle revision.
        result = await self.session.execute(
            update(FileAssetRecord)
            .where(
                FileAssetRecord.id == file_id,
                FileAssetRecord.owner_user_id == owner_user_id,
                FileAssetRecord.state == "READY",
            )
            .values(revision=FileAssetRecord.revision + 1)
            .returning(FileAssetRecord)
        )
        pinned = result.scalar_one_or_none()
        await self.session.flush()
        if pinned is None:
            file_record = await self.get_file(file_id)
            if file_record is None:
                raise KeyError(f"Unknown asset: {file_id}")
            if file_record.owner_user_id != owner_user_id:
                raise PermissionError(
                    f"Asset {file_id} is not owned by this user."
                )
            raise ValueError(f"Asset {file_id} is not READY.")

        blob_record = await self.get_blob(pinned.blob_id)
        if blob_record is None or blob_record.state != "READY":
            raise ValueError(
                f"Asset {file_id} canonical blob is not READY."
            )
        return pinned, blob_record

    @staticmethod
    def _message_part_asset_id(content: Any, index: int) -> Optional[str]:
        if not isinstance(content, list) or index < 0 or index >= len(content):
            return None
        part = content[index]
        if not isinstance(part, dict):
            return None
        data = part.get("data")
        if not isinstance(data, dict):
            return None
        attachment = (
            data.get("attachment")
            if isinstance(data.get("attachment"), dict)
            else data
        )
        if not isinstance(attachment, dict):
            return None
        return attachment.get("asset_id")

    async def create_message_references(
        self,
        *,
        message_id: str,
        owner_user_id: str,
        occurrences: Sequence[tuple[int, str]],
    ) -> list[FileReferenceRecord]:
        if not occurrences:
            return []
        message = await self.session.get(Message, message_id)
        if message is None:
            raise KeyError(f"Unknown message: {message_id}")
        from ..models.sql.chat_data.session import Session
        session = await self.session.get(Session, message.session_id)
        if session is None or session.user_id != owner_user_id:
            raise PermissionError("Message session is not owned by this user.")
        indexes = [index for index, _ in occurrences]
        if len(indexes) != len(set(indexes)):
            raise ValueError("A message content part can reference only one asset.")
        unique_file_ids = list(dict.fromkeys(file_id for _, file_id in occurrences))
        for file_id in unique_file_ids:
            await self.pin_owned_ready_file(
                file_id=file_id,
                owner_user_id=owner_user_id,
            )
        records: list[FileReferenceRecord] = []
        for index, file_id in occurrences:
            if self._message_part_asset_id(message.content, index) != file_id:
                raise ValueError(
                    "MESSAGE_CONTENT reference does not match persisted message."
                )
            records.append(
                await self._insert_reference(
                    {
                        "file_id": file_id,
                        "reference_type": "MESSAGE_CONTENT",
                        "message_id": message_id,
                        "content_part_index": index,
                    }
                )
            )
        return records

    async def list_context_file_rows(
        self,
        *,
        session_id: str,
        owner_user_id: str,
        project_id: Optional[str] = None,
    ) -> list[tuple[FileAssetRecord, FileBlobRecord]]:
        message_ids = select(Message.id).where(Message.session_id == session_id)
        predicates = [
            (
                (FileReferenceRecord.reference_type == "MESSAGE_CONTENT")
                & FileReferenceRecord.message_id.in_(message_ids)
            ),
            (
                (FileReferenceRecord.reference_type == "SESSION_RESOURCE")
                & (FileReferenceRecord.session_id == session_id)
            ),
        ]
        if project_id:
            predicates.append(
                (
                    (FileReferenceRecord.reference_type == "PROJECT_RESOURCE")
                    & (FileReferenceRecord.project_id == project_id)
                )
            )
        result = await self.session.execute(
            select(FileAssetRecord, FileBlobRecord)
            .join(
                FileReferenceRecord,
                FileReferenceRecord.file_id == FileAssetRecord.id,
            )
            .join(
                FileBlobRecord,
                FileAssetRecord.blob_id == FileBlobRecord.id,
            )
            .where(
                or_(*predicates),
                FileAssetRecord.owner_user_id == owner_user_id,
                FileAssetRecord.state == "READY",
                FileBlobRecord.state == "READY",
            )
            .order_by(FileAssetRecord.created_at.asc(), FileAssetRecord.id.asc())
        )
        dedup: dict[str, tuple[FileAssetRecord, FileBlobRecord]] = {}
        for file_record, blob_record in result.all():
            dedup.setdefault(file_record.id, (file_record, blob_record))
        return list(dedup.values())

    async def message_has_references(self, message_id: str) -> bool:
        result = await self.session.execute(
            select(FileReferenceRecord.id)
            .where(
                FileReferenceRecord.reference_type == "MESSAGE_CONTENT",
                FileReferenceRecord.message_id == message_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

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


    async def compare_and_set_file(
        self,
        file_id: str,
        *,
        expected_revision: int,
        expected_state: str,
        values: Mapping[str, Any],
    ) -> Optional[FileAssetRecord]:
        next_values = dict(values)
        next_values["revision"] = expected_revision + 1
        result = await self.session.execute(
            update(FileAssetRecord)
            .where(
                FileAssetRecord.id == file_id,
                FileAssetRecord.revision == expected_revision,
                FileAssetRecord.state == expected_state,
            )
            .values(**next_values)
            .returning(FileAssetRecord)
        )
        record = result.scalar_one_or_none()
        await self.session.flush()
        return record
