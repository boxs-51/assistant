from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from ...domain.schemas.message import GatewayMessage
from .errors import (
    MessageAccessDeniedError,
    MessageAssetStateError,
    MessageNotFoundError,
    NonCanonicalAssetContentError,
)


_ASSET_PART_TYPES = {"image", "audio", "video", "file"}
_FILE_METADATA_FIELDS = {
    "page_count",
    "language",
    "encoding",
    "created_at",
    "modified_at",
}


class CanonicalMessageService:
    """Create-only authority for canonical messages + durable asset edges."""

    def __init__(self, uow_factory) -> None:
        self.uow_factory = uow_factory

    async def persist_request_messages(
        self,
        *,
        session_id: str,
        owner_user_id: str,
        organization_id: Optional[str],
        messages: Sequence[Mapping[str, Any]],
        turn_id: str,
    ) -> bool:
        async with self.uow_factory() as uow:
            session = await uow.sessions.get_by_id(session_id)
            is_new = session is None
            if is_new:
                session = await uow.sessions.create_session(
                    user_id=owner_user_id,
                    organization_id=organization_id,
                    session_id=session_id,
                )
            elif session.user_id != owner_user_id:
                raise MessageAccessDeniedError(
                    f"Session {session_id} is not owned by this identity."
                )

            selected = list(messages) if is_new else list(messages[-1:])
            for raw in selected:
                message = (
                    raw
                    if isinstance(raw, GatewayMessage)
                    else GatewayMessage.model_validate(raw)
                )
                canonical, occurrences = await self._canonicalize_content(
                    uow,
                    owner_user_id=owner_user_id,
                    role=message.role,
                    content=message.content,
                )
                record = await uow.sessions.add_message(
                    session_id=session_id,
                    role=message.role,
                    content=canonical,
                    turn_id=turn_id,
                    completed_at=datetime.now(timezone.utc),
                )
                if occurrences:
                    await uow.assets.create_message_references(
                        message_id=record.id,
                        owner_user_id=owner_user_id,
                        occurrences=occurrences,
                    )
            await uow.commit()
            return is_new

    async def persist_message(
        self,
        *,
        session_id: str,
        role: str,
        content: Any,
        turn_id: str,
        owner_user_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
    ):
        async with self.uow_factory() as uow:
            session = await uow.sessions.get_by_id(session_id)
            if session is None:
                raise MessageNotFoundError(f"Session {session_id} not found.")
            owner = owner_user_id or session.user_id
            if not owner or session.user_id != owner:
                raise MessageAccessDeniedError(
                    f"Session {session_id} is not owned by this identity."
                )
            canonical, occurrences = await self._canonicalize_content(
                uow,
                owner_user_id=owner,
                role=role,
                content=content,
            )
            record = await uow.sessions.add_message(
                session_id=session_id,
                role=role,
                content=canonical,
                turn_id=turn_id,
                created_at=created_at,
                completed_at=completed_at,
            )
            if occurrences:
                await uow.assets.create_message_references(
                    message_id=record.id,
                    owner_user_id=owner,
                    occurrences=occurrences,
                )
            await uow.commit()
            return record

    async def _canonicalize_content(
        self,
        uow,
        *,
        owner_user_id: str,
        role: str,
        content: Any,
    ) -> tuple[Any, tuple[tuple[int, str], ...]]:
        validated = GatewayMessage(role=role, content=content)
        if isinstance(validated.content, str):
            # Preserve the existing durable text envelope while structured
            # canonical media uses the F4 JSON-list representation.
            return {"type": "text", "data": validated.content}, ()

        normalized_parts: list[dict[str, Any]] = []
        occurrences: list[tuple[int, str]] = []
        pinned: dict[str, tuple[Any, Any]] = {}

        for index, part in enumerate(validated.content):
            raw = part.model_dump(mode="json", exclude_none=True)
            part_type = part.type.value
            if part_type in _ASSET_PART_TYPES:
                attachment = self._attachment_mapping(raw, part_type)
                if not attachment:
                    raise NonCanonicalAssetContentError(
                        f"{part_type} content requires an attachment."
                    )
                asset_id = attachment.get("asset_id")
                if not asset_id or attachment.get("source") != "asset":
                    raise NonCanonicalAssetContentError(
                        f"Durable {part_type} content must reference a canonical asset."
                    )
                records = pinned.get(asset_id)
                if records is None:
                    try:
                        records = await uow.assets.get_owned_ready_file(
                            file_id=asset_id,
                            owner_user_id=owner_user_id,
                        )
                    except PermissionError as exc:
                        raise MessageAccessDeniedError(str(exc)) from exc
                    except (KeyError, ValueError) as exc:
                        raise MessageAssetStateError(str(exc)) from exc
                    pinned[asset_id] = records
                file_record, blob_record = records
                canonical = self._canonical_attachment(file_record, blob_record)
                self._replace_attachment(raw, part_type, canonical)
                occurrences.append((index, asset_id))
            normalized_parts.append(raw)

        canonical_message = GatewayMessage(role=role, content=normalized_parts)
        return (
            canonical_message.model_dump(mode="json", exclude_none=True)["content"],
            tuple(occurrences),
        )

    @staticmethod
    def _attachment_mapping(
        part: Mapping[str, Any],
        part_type: str,
    ) -> Optional[dict[str, Any]]:
        data = part.get("data")
        if not isinstance(data, dict):
            return None
        if part_type == "file" and "attachment" not in data:
            return data
        attachment = data.get("attachment")
        return attachment if isinstance(attachment, dict) else None

    @staticmethod
    def _replace_attachment(
        part: dict[str, Any],
        part_type: str,
        canonical: dict[str, Any],
    ) -> None:
        data = part.get("data")
        if not isinstance(data, dict):
            raise NonCanonicalAssetContentError(
                f"{part_type} content has invalid data."
            )
        if part_type == "file" and "attachment" not in data:
            part["data"] = canonical
        else:
            data["attachment"] = canonical

    @staticmethod
    def _canonical_attachment(file_record, blob_record) -> dict[str, Any]:
        raw_metadata = dict(file_record.metadata_json or {})
        metadata = {
            key: raw_metadata[key]
            for key in _FILE_METADATA_FIELDS
            if key in raw_metadata
        }
        if blob_record.sha256:
            metadata["sha256"] = blob_record.sha256
        return {
            "asset_id": file_record.id,
            "filename": file_record.filename,
            "mime_type": file_record.mime_type,
            "size": blob_record.size_bytes,
            "extension": file_record.extension,
            "uri": f"asset://{file_record.id}",
            "source": "asset",
            "metadata": metadata,
        }
