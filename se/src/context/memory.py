from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from se.src.context.source_identity import (
    ContextSourceRef,
    validate_context_source_ref_integrity,
)


MEMORY_IDENTITY_DOMAIN = "ctx-memory-v1"
MEMORY_SCHEMA_VERSION = 1


class MemoryRecordConflictError(RuntimeError):
    """Raised when one immutable promotion authority is replayed inconsistently."""


def _require_non_empty(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _validate_json_input(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"non-finite number at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_input(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Memory JSON object keys must be strings at {path}")
            _validate_json_input(item, path=f"{path}.{key}")
        return
    raise ValueError(f"Memory values must use canonical JSON containers at {path}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_frozen_json(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"non-finite number at {path}")
        return
    if isinstance(value, MappingProxyType):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"Memory frozen JSON keys must be strings at {path}")
            _validate_frozen_json(item, path=f"{path}.{key}")
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_frozen_json(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"Memory contains a non-frozen JSON container at {path}")


def canonical_memory_bytes(value: Any) -> bytes:
    """Return deterministic canonical JSON bytes for the dormant Memory domain."""
    _validate_json_input(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Memory values must be canonical JSON.") from exc
    return encoded.encode("utf-8")


def memory_content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_memory_bytes(value)).hexdigest()


def memory_id(
    *,
    source_ref: ContextSourceRef,
    promotion_authority_id: str,
    content_digest: str,
    memory_schema_version: int = MEMORY_SCHEMA_VERSION,
) -> str:
    validate_context_source_ref_integrity(source_ref)
    promotion_authority_id = _require_non_empty(
        "promotion_authority_id",
        promotion_authority_id,
    )
    content_digest = _require_non_empty("content_digest", content_digest)
    if isinstance(memory_schema_version, bool) or not isinstance(
        memory_schema_version,
        int,
    ):
        raise ValueError("memory_schema_version must be an integer")
    if memory_schema_version < 1:
        raise ValueError("memory_schema_version must be >= 1")

    material = {
        "owner_user_id": source_ref.owner_user_id,
        "promotion_authority_id": promotion_authority_id,
        "source_context_source_id": source_ref.context_source_id,
        "content_digest": content_digest,
        "memory_schema_version": memory_schema_version,
    }
    return hashlib.sha256(
        MEMORY_IDENTITY_DOMAIN.encode("utf-8")
        + b"\x00"
        + canonical_memory_bytes(material)
    ).hexdigest()


class MemoryRecord(BaseModel):
    """Dormant immutable CTX-F5 Memory value; not durable/runtime promotion authority."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    memory_id: str
    promotion_authority_id: str
    memory_schema_version: int = Field(default=MEMORY_SCHEMA_VERSION, ge=1)
    source_ref_snapshot: ContextSourceRef
    owner_user_id: str
    content_digest: str
    canonical_bytes: int = Field(ge=0)
    content: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict, validate_default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator(
        "memory_id",
        "promotion_authority_id",
        "owner_user_id",
        "content_digest",
        mode="before",
    )
    @classmethod
    def normalize_required_strings(cls, value: Any, info) -> str:
        return _require_non_empty(info.field_name, value)

    @field_validator("content", mode="before")
    @classmethod
    def validate_content_domain(cls, value: Any) -> Any:
        canonical_memory_bytes(value)
        return value

    @field_validator("content", mode="after")
    @classmethod
    def freeze_content(cls, value: Any) -> Any:
        return _freeze_json(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata_domain(cls, value: Any) -> Any:
        canonical_memory_bytes(value)
        return value

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: dict[str, Any]) -> MappingProxyType:
        return _freeze_json(value)

    @model_validator(mode="after")
    def validate_integrity(self) -> "MemoryRecord":
        return validate_memory_record_integrity(self)

    @field_serializer("content", "metadata")
    def serialize_frozen_json(self, value: Any) -> Any:
        return _thaw_json(value)


def _validate_stored_record_shape(record: MemoryRecord) -> None:
    declared_fields = set(type(record).model_fields)
    stored_fields = set(record.__dict__)
    extra_fields = stored_fields - declared_fields
    if record.__pydantic_extra__:
        extra_fields.update(record.__pydantic_extra__)
    if extra_fields:
        joined = ", ".join(sorted(extra_fields))
        raise ValueError(f"MemoryRecord contains undeclared stored fields: {joined}")

    if not isinstance(record.source_ref_snapshot, ContextSourceRef):
        raise ValueError("source_ref_snapshot must be a ContextSourceRef")
    if isinstance(record.memory_schema_version, bool) or not isinstance(
        record.memory_schema_version,
        int,
    ):
        raise ValueError("memory_schema_version must be an integer")
    if record.memory_schema_version < 1:
        raise ValueError("memory_schema_version must be >= 1")
    if isinstance(record.canonical_bytes, bool) or not isinstance(
        record.canonical_bytes,
        int,
    ):
        raise ValueError("canonical_bytes must be an integer")
    if record.canonical_bytes < 0:
        raise ValueError("canonical_bytes must be >= 0")
    if not isinstance(record.created_at, datetime):
        raise ValueError("created_at must be a datetime")

    _validate_frozen_json(record.content, path="$.content")
    if not isinstance(record.metadata, MappingProxyType):
        raise ValueError("metadata root must be a frozen JSON object")
    _validate_frozen_json(record.metadata, path="$.metadata")


def validate_memory_record_integrity(record: MemoryRecord) -> MemoryRecord:
    _validate_stored_record_shape(record)

    memory_value = _require_non_empty("memory_id", record.memory_id)
    promotion_value = _require_non_empty(
        "promotion_authority_id",
        record.promotion_authority_id,
    )
    owner_value = _require_non_empty("owner_user_id", record.owner_user_id)
    digest_value = _require_non_empty("content_digest", record.content_digest)

    if record.memory_id != memory_value:
        raise ValueError("memory_id must already be normalized")
    if record.promotion_authority_id != promotion_value:
        raise ValueError("promotion_authority_id must already be normalized")
    if record.owner_user_id != owner_value:
        raise ValueError("owner_user_id must already be normalized")
    if record.content_digest != digest_value:
        raise ValueError("content_digest must already be normalized")

    validate_context_source_ref_integrity(record.source_ref_snapshot)

    if record.owner_user_id != record.source_ref_snapshot.owner_user_id:
        raise ValueError("Memory owner must equal source provenance owner")

    canonical = canonical_memory_bytes(_thaw_json(record.content))
    expected_digest = hashlib.sha256(canonical).hexdigest()
    if record.content_digest != expected_digest:
        raise ValueError("Memory content_digest does not match content")
    if record.canonical_bytes != len(canonical):
        raise ValueError("Memory canonical_bytes does not match content")

    expected_id = memory_id(
        source_ref=record.source_ref_snapshot,
        promotion_authority_id=record.promotion_authority_id,
        content_digest=expected_digest,
        memory_schema_version=record.memory_schema_version,
    )
    if record.memory_id != expected_id:
        raise ValueError("memory_id does not match frozen identity material")

    return record


def create_memory_record(
    *,
    source_ref: ContextSourceRef,
    promotion_authority_id: str,
    content: Any,
    metadata: Mapping[str, Any] | None = None,
    memory_schema_version: int = MEMORY_SCHEMA_VERSION,
) -> MemoryRecord:
    """Create a dormant Memory value from already-authorized provenance input."""
    validate_context_source_ref_integrity(source_ref)
    canonical = canonical_memory_bytes(content)
    digest = hashlib.sha256(canonical).hexdigest()
    record_id = memory_id(
        source_ref=source_ref,
        promotion_authority_id=promotion_authority_id,
        content_digest=digest,
        memory_schema_version=memory_schema_version,
    )
    return MemoryRecord(
        memory_id=record_id,
        promotion_authority_id=promotion_authority_id,
        memory_schema_version=memory_schema_version,
        source_ref_snapshot=source_ref,
        owner_user_id=source_ref.owner_user_id,
        content_digest=digest,
        canonical_bytes=len(canonical),
        content=content,
        metadata=dict(metadata or {}),
    )


class MemoryRecordRepository(Protocol):
    async def put(self, record: MemoryRecord) -> MemoryRecord:
        ...

    async def get(self, memory_id: str) -> MemoryRecord | None:
        ...

    async def get_by_promotion_authority(
        self,
        promotion_authority_id: str,
    ) -> MemoryRecord | None:
        ...


def _immutable_record_canonical_bytes(record: MemoryRecord) -> bytes:
    return canonical_memory_bytes(
        record.model_dump(mode="json", exclude={"created_at"})
    )


def _same_immutable_record(left: MemoryRecord, right: MemoryRecord) -> bool:
    return _immutable_record_canonical_bytes(left) == _immutable_record_canonical_bytes(
        right
    )


class InMemoryMemoryRecordRepository:
    """Process-local append-only reference repository; not durable Memory storage."""

    def __init__(self) -> None:
        self._by_id: dict[str, MemoryRecord] = {}
        self._promotion_to_id: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: MemoryRecord) -> MemoryRecord:
        validate_memory_record_integrity(record)

        async with self._lock:
            mapped_id = self._promotion_to_id.get(record.promotion_authority_id)
            if mapped_id is not None and mapped_id != record.memory_id:
                raise MemoryRecordConflictError(
                    "promotion_authority_id already maps to a different Memory identity"
                )

            existing = self._by_id.get(record.memory_id)
            if existing is not None:
                if not _same_immutable_record(existing, record):
                    raise MemoryRecordConflictError(
                        "memory_id already exists with conflicting immutable record"
                    )
                return existing

            self._by_id[record.memory_id] = record
            self._promotion_to_id[record.promotion_authority_id] = record.memory_id
            return record

    async def get(self, memory_id: str) -> MemoryRecord | None:
        return self._by_id.get(memory_id)

    async def get_by_promotion_authority(
        self,
        promotion_authority_id: str,
    ) -> MemoryRecord | None:
        memory_id_value = self._promotion_to_id.get(promotion_authority_id)
        if memory_id_value is None:
            return None
        return self._by_id.get(memory_id_value)
