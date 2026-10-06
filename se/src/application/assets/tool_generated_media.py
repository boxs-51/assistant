from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from sqlalchemy import select

from ...infrastructure.storage.models.sql.agent.execution import (
    AgentExecutionRecord,
)
from ...infrastructure.storage.models.sql.agent.tool_call import (
    AgentToolCallRecord,
)
from ...infrastructure.storage.models.sql.agent.tool_result import (
    AgentToolResultRecord,
)
from ...infrastructure.storage.models.sql.capability.invocation import (
    CapabilityInvocationRecord,
)
from ...infrastructure.storage.models.sql.chat_data.session import Session


F7T_INLINE_BASE64_V1 = "F7T_INLINE_BASE64_V1"
EMPTY_F7T_ENROLLMENT: Mapping[tuple[str, str], str] = MappingProxyType({})
F7T_A1_ENROLLMENT: Mapping[tuple[str, str], str] = MappingProxyType(
    {("desktop.screenshot", "1.0"): F7T_INLINE_BASE64_V1}
)
F7T_A1_MAX_MEDIA_ITEMS = 8
F7T_A1_MEDIA_KIND = "image"
F7T_A1_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)


class ToolGeneratedMediaRejectedError(ValueError):
    """Committed tool result is not an admitted F7-T source."""


class ToolGeneratedMediaAmbiguousError(RuntimeError):
    """A prior or current F7-T ingest may already have produced a side effect."""


@dataclass(frozen=True, slots=True)
class ToolMediaItem:
    ordinal: int
    media_kind: str
    mime_type: str
    filename: str
    size_bytes: int
    sha256: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ToolMediaProjection:
    projection_id: str
    origin_id: str
    asset_id: str | None
    state: str


def _canonical_string(name: str, value: object, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise ToolGeneratedMediaRejectedError(f"{name} must be a string")
    if not value or value != value.strip() or len(value) > max_length:
        raise ToolGeneratedMediaRejectedError(f"{name} is not canonical")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ToolGeneratedMediaRejectedError(f"{name} contains control characters")
    return value


def _source_key_digest(
    *,
    source_result_id: str,
    invocation_id: str,
    tool_call_id: str,
    capability_id: str,
    media_ordinal: int,
) -> str:
    encoded = json.dumps(
        {
            "capability_id": capability_id,
            "invocation_id": invocation_id,
            "media_ordinal": media_ordinal,
            "source_result_id": source_result_id,
            "tool_call_id": tool_call_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def projection_id_for_source_key(**kwargs) -> str:
    return "f7tp_" + _source_key_digest(**kwargs)


def origin_id_for_source_key(**kwargs) -> str:
    return "f7t:v1:" + _source_key_digest(**kwargs)


def _decode_media_items(
    output: object,
    *,
    expected_contract: str,
    max_media_bytes: int,
) -> tuple[ToolMediaItem, ...]:
    if not isinstance(output, dict) or set(output) != {
        "ok",
        "tool",
        "action",
        "data",
        "error",
        "meta",
    }:
        raise ToolGeneratedMediaRejectedError("tool output is not canonical ToolResult")
    if output["ok"] is not True or output["error"] is not None:
        raise ToolGeneratedMediaRejectedError("tool output is not successful")
    _canonical_string("tool", output["tool"], max_length=255)
    _canonical_string("action", output["action"], max_length=255)

    meta = output["meta"]
    if not isinstance(meta, dict):
        raise ToolGeneratedMediaRejectedError("ToolResult.meta must be an object")
    if set(("version", "truncated", "warnings")) - set(meta):
        raise ToolGeneratedMediaRejectedError("ToolResult.meta is incomplete")
    _canonical_string("meta.version", meta["version"], max_length=64)
    if meta["truncated"] is not False:
        raise ToolGeneratedMediaRejectedError("truncated output is not F7-T authority")
    if not isinstance(meta["warnings"], list) or not all(
        isinstance(item, str) and bool(item) for item in meta["warnings"]
    ):
        raise ToolGeneratedMediaRejectedError("ToolResult.meta.warnings is invalid")

    data = output["data"]
    if not isinstance(data, dict) or set(data) != {"$f7t_media"}:
        raise ToolGeneratedMediaRejectedError("ToolResult.data is not F7-T media")
    media = data["$f7t_media"]
    if not isinstance(media, dict) or set(media) != {"contract", "items"}:
        raise ToolGeneratedMediaRejectedError("F7-T media envelope is malformed")
    if media["contract"] != expected_contract:
        raise ToolGeneratedMediaRejectedError("F7-T source contract mismatch")
    items = media["items"]
    if not isinstance(items, list) or not items:
        raise ToolGeneratedMediaRejectedError("F7-T media items must be non-empty")
    if len(items) > F7T_A1_MAX_MEDIA_ITEMS:
        raise ToolGeneratedMediaRejectedError("F7-T media item count exceeds bound")

    decoded: list[ToolMediaItem] = []
    aggregate_decoded_bytes = 0
    expected_keys = {
        "ordinal",
        "media_kind",
        "mime_type",
        "filename",
        "encoding",
        "size_bytes",
        "sha256",
        "data_base64",
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != expected_keys:
            raise ToolGeneratedMediaRejectedError("F7-T media item is malformed")
        if type(item["ordinal"]) is not int or item["ordinal"] != index:
            raise ToolGeneratedMediaRejectedError("F7-T media ordinal mismatch")
        media_kind = _canonical_string(
            "media_kind", item["media_kind"], max_length=64
        )
        mime_type = _canonical_string(
            "mime_type", item["mime_type"], max_length=255
        )
        if media_kind != F7T_A1_MEDIA_KIND:
            raise ToolGeneratedMediaRejectedError(
                "F7-T media kind is not admitted"
            )
        if (
            mime_type != mime_type.lower()
            or mime_type not in F7T_A1_MIME_TYPES
        ):
            raise ToolGeneratedMediaRejectedError(
                "F7-T MIME type is not admitted"
            )
        filename = _canonical_string(
            "filename", item["filename"], max_length=1024
        )
        if "/" in filename or "\\" in filename:
            raise ToolGeneratedMediaRejectedError(
                "filename must not carry path authority"
            )
        if item["encoding"] != "base64":
            raise ToolGeneratedMediaRejectedError("F7-T transport must be base64")
        size_bytes = item["size_bytes"]
        if type(size_bytes) is not int or size_bytes < 0:
            raise ToolGeneratedMediaRejectedError("size_bytes is invalid")
        if size_bytes > max_media_bytes:
            raise ToolGeneratedMediaRejectedError("F7-T media exceeds byte bound")
        digest = item["sha256"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or digest.lower() != digest
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ToolGeneratedMediaRejectedError("sha256 is not canonical")
        encoded = item["data_base64"]
        if not isinstance(encoded, str):
            raise ToolGeneratedMediaRejectedError("data_base64 must be a string")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ToolGeneratedMediaRejectedError(
                "data_base64 is not strict base64"
            ) from exc
        if base64.b64encode(payload).decode("ascii") != encoded:
            raise ToolGeneratedMediaRejectedError(
                "data_base64 is not canonical base64"
            )
        if len(payload) != size_bytes:
            raise ToolGeneratedMediaRejectedError("size_bytes mismatch")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ToolGeneratedMediaRejectedError("sha256 mismatch")
        aggregate_decoded_bytes += len(payload)
        if aggregate_decoded_bytes > max_media_bytes:
            raise ToolGeneratedMediaRejectedError(
                "F7-T aggregate media exceeds byte bound"
            )
        decoded.append(
            ToolMediaItem(
                ordinal=index,
                media_kind=media_kind,
                mime_type=mime_type,
                filename=filename,
                size_bytes=size_bytes,
                sha256=digest,
                payload=payload,
            )
        )
    return tuple(decoded)


class ToolGeneratedMediaCanonicalizer:
    """Canonicalize admitted durable tool-generated media into CAS assets.

    Enrollment is explicit and immutable. The default remains empty so callers
    must opt into a frozen producer tuple instead of inferring media authority.
    """

    def __init__(
        self,
        *,
        uow_factory,
        asset_service,
        enrollment: Mapping[tuple[str, str], str] = EMPTY_F7T_ENROLLMENT,
        max_media_bytes: int,
    ) -> None:
        if max_media_bytes <= 0:
            raise ValueError("max_media_bytes must be positive")
        copied = dict(enrollment)
        for key, contract in copied.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or not all(isinstance(part, str) and part for part in key)
                or contract != F7T_INLINE_BASE64_V1
            ):
                raise ValueError("invalid F7-T enrollment")
        self._uow_factory = uow_factory
        self._asset_service = asset_service
        self._enrollment = MappingProxyType(copied)
        self._max_media_bytes = max_media_bytes

    async def canonicalize_committed_result(
        self,
        *,
        source_result_id: str,
    ) -> tuple[ToolMediaProjection, ...]:
        source = await self._read_source(source_result_id)
        contract = self._enrollment.get(
            (source["capability_id"], source["capability_version"])
        )
        if contract is None:
            return ()
        items = _decode_media_items(
            source["output"],
            expected_contract=contract,
            max_media_bytes=self._max_media_bytes,
        )
        projections = []
        for item in items:
            projections.append(await self._canonicalize_item(source, item, contract))
        return tuple(projections)

    async def _read_source(self, source_result_id: str) -> dict[str, object]:
        async with self._uow_factory() as uow:
            result = await uow.session.get(AgentToolResultRecord, source_result_id)
            if result is None:
                raise ToolGeneratedMediaRejectedError("tool result was not found")
            if (
                result.commit_state != "COMMITTED"
                or result.success is not True
                or result.error_code is not None
                or result.error_message is not None
                or result.retryable is not False
            ):
                raise ToolGeneratedMediaRejectedError(
                    "tool result is not exact successful COMMITTED authority"
                )
            execution = await uow.session.get(
                AgentExecutionRecord, result.execution_id
            )
            if execution is None:
                raise ToolGeneratedMediaRejectedError("execution was not found")
            canonical_session = await uow.session.get(
                Session, execution.session_id
            )
            if canonical_session is None or not canonical_session.user_id:
                raise ToolGeneratedMediaRejectedError("canonical owner was not found")
            invocation = await uow.session.get(
                CapabilityInvocationRecord, result.invocation_id
            )
            if invocation is None:
                raise ToolGeneratedMediaRejectedError("invocation was not found")
            tool_call_query = await uow.session.execute(
                select(AgentToolCallRecord).where(
                    AgentToolCallRecord.execution_id == result.execution_id,
                    AgentToolCallRecord.tool_call_id == result.tool_call_id,
                )
            )
            tool_call = tool_call_query.scalar_one_or_none()
            if tool_call is None:
                raise ToolGeneratedMediaRejectedError("tool call was not found")

            capability_version = _canonical_string(
                "invocation.capability_version",
                invocation.capability_version,
                max_length=64,
            )
            if not (
                invocation.invocation_id == result.invocation_id
                and invocation.capability_id == result.capability_id
                and invocation.execution_id == result.execution_id
                and invocation.tool_call_id == result.tool_call_id
                and invocation.kind == "TOOL"
                and invocation.state == "COMPLETED"
                and invocation.session_id == execution.session_id
                and invocation.owner_user_id == canonical_session.user_id
                and tool_call.execution_id == result.execution_id
                and tool_call.iteration_id == result.iteration_id
                and tool_call.invocation_id == result.invocation_id
                and tool_call.tool_call_id == result.tool_call_id
                and tool_call.capability_id == result.capability_id
            ):
                raise ToolGeneratedMediaRejectedError(
                    "durable tool-result lineage mismatch"
                )
            return {
                "source_result_id": result.id,
                "execution_id": result.execution_id,
                "invocation_id": result.invocation_id,
                "tool_call_id": result.tool_call_id,
                "capability_id": result.capability_id,
                "capability_version": capability_version,
                "owner_user_id": canonical_session.user_id,
                "output": result.output,
            }

    async def _canonicalize_item(
        self,
        source: dict[str, object],
        item: ToolMediaItem,
        source_contract_id: str,
    ) -> ToolMediaProjection:
        key = {
            "source_result_id": str(source["source_result_id"]),
            "invocation_id": str(source["invocation_id"]),
            "tool_call_id": str(source["tool_call_id"]),
            "capability_id": str(source["capability_id"]),
            "media_ordinal": item.ordinal,
        }
        projection_id = projection_id_for_source_key(**key)
        origin_id = origin_id_for_source_key(**key)

        async with self._uow_factory() as uow:
            record, created = (
                await uow.assets.try_create_tool_media_projection_reservation(
                    {
                        "id": projection_id,
                        **key,
                        "execution_id": source["execution_id"],
                        "capability_version": source["capability_version"],
                        "source_contract_id": source_contract_id,
                        "owner_user_id": source["owner_user_id"],
                        "origin_id": origin_id,
                        "state": "RESERVED",
                        "asset_id": None,
                        "revision": 0,
                    }
                )
            )
            if created:
                await uow.commit()
            else:
                self._verify_existing(record, source, origin_id, source_contract_id)
                return self._existing_projection(record)

        async with self._uow_factory() as uow:
            winner = await uow.assets.compare_and_set_tool_media_projection(
                projection_id,
                expected_revision=0,
                expected_state="RESERVED",
                values={"state": "INGESTING"},
            )
            if winner is None:
                existing = await uow.assets.get_tool_media_projection_by_source_key(
                    **key
                )
                if existing is None:
                    raise ToolGeneratedMediaAmbiguousError(
                        "projection disappeared after reservation"
                    )
                self._verify_existing(
                    existing, source, origin_id, source_contract_id
                )
                return self._existing_projection(existing)
            await uow.commit()

        async def stream():
            yield item.payload

        try:
            descriptor = await self._asset_service.ingest_stream(
                owner_user_id=str(source["owner_user_id"]),
                filename=item.filename,
                mime_type=item.mime_type,
                stream=stream(),
                content_length=item.size_bytes,
                max_bytes=self._max_media_bytes,
                origin_type="TOOL",
                origin_id=origin_id,
                metadata={
                    "f7t_source_result_id": source["source_result_id"],
                    "f7t_media_ordinal": item.ordinal,
                    "f7t_source_contract_id": source_contract_id,
                    "f7t_media_kind": item.media_kind,
                    "f7t_sha256": item.sha256,
                },
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._mark_ambiguous(projection_id))
            raise
        except Exception:
            await self._mark_ambiguous(projection_id)
            raise

        async with self._uow_factory() as uow:
            ready = await uow.assets.compare_and_set_tool_media_projection(
                projection_id,
                expected_revision=1,
                expected_state="INGESTING",
                values={"state": "READY", "asset_id": descriptor.asset_id},
            )
            if ready is None:
                raise ToolGeneratedMediaAmbiguousError(
                    "projection READY publication lost durable CAS"
                )
            await uow.commit()
            return ToolMediaProjection(
                projection_id=ready.id,
                origin_id=ready.origin_id,
                asset_id=ready.asset_id,
                state=ready.state,
            )

    async def _mark_ambiguous(self, projection_id: str) -> None:
        async with self._uow_factory() as uow:
            current = await uow.session.get(
                __import__(
                    "se.src.infrastructure.storage.models.sql.assets",
                    fromlist=["ToolMediaAssetProjectionRecord"],
                ).ToolMediaAssetProjectionRecord,
                projection_id,
            )
            if current is None:
                return
            if current.state == "INGESTING":
                await uow.assets.compare_and_set_tool_media_projection(
                    projection_id,
                    expected_revision=current.revision,
                    expected_state="INGESTING",
                    values={"state": "AMBIGUOUS"},
                )
                await uow.commit()

    @staticmethod
    def _verify_existing(
        record,
        source: dict[str, object],
        origin_id: str,
        source_contract_id: str,
    ) -> None:
        if not (
            record.execution_id == source["execution_id"]
            and record.capability_version == source["capability_version"]
            and record.owner_user_id == source["owner_user_id"]
            and record.source_contract_id == source_contract_id
            and record.origin_id == origin_id
        ):
            raise ToolGeneratedMediaAmbiguousError(
                "durable projection lineage mismatch"
            )

    @staticmethod
    def _existing_projection(record) -> ToolMediaProjection:
        if record.state == "READY":
            if not record.asset_id:
                raise ToolGeneratedMediaAmbiguousError(
                    "READY projection has no asset_id"
                )
            return ToolMediaProjection(
                projection_id=record.id,
                origin_id=record.origin_id,
                asset_id=record.asset_id,
                state="READY",
            )
        if record.state in {"INGESTING", "AMBIGUOUS"}:
            raise ToolGeneratedMediaAmbiguousError(
                f"projection is terminally unsafe to re-ingest: {record.state}"
            )
        if record.state == "RESERVED":
            raise ToolGeneratedMediaAmbiguousError(
                "projection reservation already exists; no duplicate ingest"
            )
        raise ToolGeneratedMediaAmbiguousError("unknown projection state")
