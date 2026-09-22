from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.sql.agent.execution import AgentExecutionRecord
from ..models.sql.agent.tool_result import AgentToolResultRecord
from ..models.sql.chat_data.session import Message
from ..models.sql.assets import (
    FileAssetRecord,
    FileBlobRecord,
    FileProviderBindingRecord,
    FileReferenceRecord,
)


class AssetRepository:
    """Transaction-scoped persistence for Central Asset Storage."""

    _LIVE_EXECUTION_STATES = ("CREATED", "RUNNING", "WAITING")

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

    async def list_owned_file_rows(
        self,
        owner_user_id: str,
        *,
        states: Optional[Sequence[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[FileAssetRecord, FileBlobRecord]]:
        statement = (
            select(FileAssetRecord, FileBlobRecord)
            .join(FileBlobRecord, FileAssetRecord.blob_id == FileBlobRecord.id)
            .where(FileAssetRecord.owner_user_id == owner_user_id)
            .order_by(
                FileAssetRecord.created_at.desc(),
                FileAssetRecord.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        if states:
            statement = statement.where(
                FileAssetRecord.state.in_(tuple(states))
            )
        result = await self.session.execute(statement)
        return list(result.all())

    async def count_files_by_owner(
        self,
        owner_user_id: str,
        *,
        states: Optional[Sequence[str]] = None,
    ) -> int:
        statement = (
            select(func.count(FileAssetRecord.id))
            .where(FileAssetRecord.owner_user_id == owner_user_id)
        )
        if states:
            statement = statement.where(
                FileAssetRecord.state.in_(tuple(states))
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
        protected = {"AGENT_TOOL_RESULT", "MESSAGE_CONTENT"}
        if values.get("reference_type") in protected:
            raise ValueError(
                "Use the dedicated reference authority for "
                f"{values.get('reference_type')}."
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
        file_record, blob_record = await self.get_owned_ready_file(
            file_id=file_id,
            owner_user_id=owner_user_id,
        )
        pinned = await self.compare_and_set_file(
            file_id,
            expected_revision=file_record.revision,
            expected_state="READY",
            values={"state": "READY"},
        )
        if pinned is None:
            raise ValueError(
                f"Asset {file_id} changed while creating a message reference."
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

    async def delete_message_references(self, message_id: str) -> None:
        await self.session.execute(
            delete(FileReferenceRecord).where(
                FileReferenceRecord.reference_type == "MESSAGE_CONTENT",
                FileReferenceRecord.message_id == message_id,
            )
        )
        await self.session.flush()

    async def create_tool_result_reference(
        self,
        *,
        file_id: str,
        agent_tool_result_id: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> FileReferenceRecord:
        file_record = await self.get_file(file_id)
        if file_record is None:
            raise KeyError(f"Unknown asset: {file_id}")
        if file_record.state != "READY":
            raise ValueError(
                f"Asset {file_id} is not READY and cannot gain an R7 reference."
            )

        tool_result = await self.session.get(
            AgentToolResultRecord,
            agent_tool_result_id,
        )
        if tool_result is None:
            raise KeyError(
                f"Unknown AgentToolResult: {agent_tool_result_id}"
            )
        if tool_result.commit_state != "COMMITTED":
            raise ValueError(
                "PROVISIONAL AgentToolResult cannot hold a durable asset reference."
            )

        existing = await self.session.execute(
            select(FileReferenceRecord)
            .where(
                FileReferenceRecord.file_id == file_id,
                FileReferenceRecord.reference_type == "AGENT_TOOL_RESULT",
                FileReferenceRecord.agent_tool_result_id
                == agent_tool_result_id,
            )
            .limit(1)
        )
        record = existing.scalar_one_or_none()
        if record is not None:
            return record

        # Fence deletion against a concurrent R7 pin. The READY -> READY CAS
        # intentionally advances the asset revision in the same transaction as
        # the reference insert. A concurrent delete using the old revision
        # loses; a delete that wins first changes state away from READY so this
        # pin fails closed.
        pinned = await self.compare_and_set_file(
            file_id,
            expected_revision=file_record.revision,
            expected_state="READY",
            values={"state": "READY"},
        )
        if pinned is None:
            raise ValueError(
                f"Asset {file_id} changed while creating an R7 reference."
            )

        return await self._insert_reference(
            {
                "file_id": file_id,
                "reference_type": "AGENT_TOOL_RESULT",
                "agent_tool_result_id": agent_tool_result_id,
                "metadata_json": dict(metadata or {}),
            }
        )

    async def list_references_for_file(
        self, file_id: str
    ) -> list[FileReferenceRecord]:
        result = await self.session.execute(
            select(FileReferenceRecord)
            .where(FileReferenceRecord.file_id == file_id)
            .order_by(FileReferenceRecord.created_at.asc())
        )
        return list(result.scalars().all())


    async def list_reference_details(
        self,
        file_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self.list_references_for_file(file_id)
        result: list[dict[str, Any]] = []
        for reference in rows:
            item: dict[str, Any] = {
                "reference_id": reference.id,
                "asset_id": reference.file_id,
                "reference_type": reference.reference_type,
                "created_at": reference.created_at,
                "message_id": reference.message_id,
                "session_id": reference.session_id,
                "project_id": reference.project_id,
                "content_part_index": reference.content_part_index,
                "agent_tool_result_id": reference.agent_tool_result_id,
                "metadata": dict(reference.metadata_json or {}),
            }
            if reference.reference_type == "MESSAGE_CONTENT":
                message = await self.session.get(Message, reference.message_id)
                if message is not None:
                    item.update(
                        session_id=message.session_id,
                        turn_id=message.turn_id,
                        sequence=message.sequence,
                        role=message.role,
                    )
            elif reference.reference_type == "AGENT_TOOL_RESULT":
                tool_result = await self.session.get(
                    AgentToolResultRecord,
                    reference.agent_tool_result_id,
                )
                if tool_result is not None:
                    item.update(
                        execution_id=tool_result.execution_id,
                        tool_call_id=tool_result.tool_call_id,
                        invocation_id=tool_result.invocation_id,
                        capability_id=tool_result.capability_id,
                        commit_state=tool_result.commit_state,
                    )
                    execution = await self.session.get(
                        AgentExecutionRecord,
                        tool_result.execution_id,
                    )
                    if execution is not None:
                        item["session_id"] = execution.session_id
            result.append(item)
        return result

    async def list_live_references(
        self,
        file_id: str,
    ) -> list[FileReferenceRecord]:
        """Return R7 references that still pin bytes for a live execution."""
        tool_result = await self.session.execute(
            select(FileReferenceRecord)
            .join(
                AgentToolResultRecord,
                FileReferenceRecord.agent_tool_result_id
                == AgentToolResultRecord.id,
            )
            .join(
                AgentExecutionRecord,
                AgentToolResultRecord.execution_id
                == AgentExecutionRecord.id,
            )
            .where(
                FileReferenceRecord.file_id == file_id,
                FileReferenceRecord.reference_type == "AGENT_TOOL_RESULT",
                AgentToolResultRecord.commit_state == "COMMITTED",
                AgentExecutionRecord.state.in_(self._LIVE_EXECUTION_STATES),
            )
        )

        message_result = await self.session.execute(
            select(FileReferenceRecord)
            .join(
                Message,
                FileReferenceRecord.message_id == Message.id,
            )
            .join(
                AgentExecutionRecord,
                Message.session_id == AgentExecutionRecord.session_id,
            )
            .where(
                FileReferenceRecord.file_id == file_id,
                FileReferenceRecord.reference_type == "MESSAGE_CONTENT",
                AgentExecutionRecord.state.in_(self._LIVE_EXECUTION_STATES),
            )
        )

        dedup: dict[str, FileReferenceRecord] = {}
        for record in [
            *tool_result.scalars().all(),
            *message_result.scalars().all(),
        ]:
            dedup[record.id] = record
        return sorted(
            dedup.values(),
            key=lambda item: (item.created_at, item.id),
        )

    async def has_live_references(self, file_id: str) -> bool:
        rows = await self.list_live_references(file_id)
        return bool(rows)

    async def list_context_file_rows(
        self,
        *,
        session_id: str,
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
                FileAssetRecord.state == "READY",
                FileBlobRecord.state == "READY",
            )
            .order_by(FileAssetRecord.created_at.asc(), FileAssetRecord.id.asc())
        )
        dedup: dict[str, tuple[FileAssetRecord, FileBlobRecord]] = {}
        for file_record, blob_record in result.all():
            dedup.setdefault(file_record.id, (file_record, blob_record))
        return list(dedup.values())

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

    async def compare_and_set_blob_state(
        self,
        blob_id: str,
        *,
        expected_state: str,
        values: Mapping[str, Any],
    ) -> Optional[FileBlobRecord]:
        result = await self.session.execute(
            update(FileBlobRecord)
            .where(
                FileBlobRecord.id == blob_id,
                FileBlobRecord.state == expected_state,
            )
            .values(**dict(values))
            .returning(FileBlobRecord)
        )
        record = result.scalar_one_or_none()
        await self.session.flush()
        return record
