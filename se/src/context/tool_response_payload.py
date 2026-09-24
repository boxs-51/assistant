from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Dict, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)


TOOL_RESPONSE_PAYLOAD_IDENTITY_DOMAIN = "ctx-tool-response-payload-v1"
TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION = 1
COMMITTED_RESULT_STATE = "COMMITTED"


class ToolResponsePayloadConflictError(RuntimeError):
    """Raised when one immutable source identity is reused inconsistently."""


def _validate_json_input(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_input(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"ToolResponsePayload JSON object keys must be strings at {path}."
                )
            _validate_json_input(item, path=f"{path}.{key}")
        return
    raise ValueError(
        f"ToolResponsePayload values must use canonical JSON containers at {path}."
    )


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _validate_frozen_json(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, MappingProxyType):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"ToolResponsePayload frozen JSON keys must be strings at {path}."
                )
            _validate_frozen_json(item, path=f"{path}.{key}")
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_frozen_json(item, path=f"{path}[{index}]")
        return
    raise ValueError(
        f"ToolResponsePayload contains a non-frozen JSON container at {path}."
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def canonical_payload_bytes(value: Any) -> bytes:
    """Return canonical JSON bytes for supported CTX-F1 values."""
    _validate_json_input(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("ToolResponsePayload values must be canonical JSON.") from exc
    return encoded.encode("utf-8")


def tool_response_content_digest(value: Any) -> str:
    return hashlib.sha256(canonical_payload_bytes(value)).hexdigest()


def tool_response_payload_id(
    *,
    source_result_id: str,
    invocation_id: str,
    execution_id: str,
    tool_call_id: str,
    logical_capability_id: str,
    content_digest: str,
    payload_schema_version: int = TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
) -> str:
    if payload_schema_version < 1:
        raise ValueError("payload_schema_version must be >= 1")
    identity = {
        "source_result_id": source_result_id,
        "invocation_id": invocation_id,
        "execution_id": execution_id,
        "tool_call_id": tool_call_id,
        "logical_capability_id": logical_capability_id,
        "payload_schema_version": payload_schema_version,
        "content_digest": content_digest,
    }
    material = (
        TOOL_RESPONSE_PAYLOAD_IDENTITY_DOMAIN.encode("utf-8")
        + b"\x00"
        + canonical_payload_bytes(identity)
    )
    return hashlib.sha256(material).hexdigest()


class ToolResponsePayload(BaseModel):
    """Immutable CTX-F1 identity/provenance record for one committed tool result."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    payload_id: str
    source_result_id: str
    invocation_id: str
    execution_id: str
    tool_call_id: str
    logical_capability_id: str
    source_commit_state: str
    payload_schema_version: int = Field(default=TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION, ge=1)
    content_digest: str
    canonical_bytes: int = Field(ge=0)
    content: Any = None
    content_type: str = "application/json"
    owner_user_id: str | None = None
    session_id: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("content", mode="before")
    @classmethod
    def validate_content_json_domain(cls, value: Any) -> Any:
        canonical_payload_bytes(value)
        return value

    @field_validator("content", mode="after")
    @classmethod
    def freeze_content(cls, value: Any) -> Any:
        return _freeze_json(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata_json_domain(cls, value: Any) -> Any:
        canonical_payload_bytes(value)
        return value

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: Dict[str, Any]) -> MappingProxyType:
        return _freeze_json(value)

    @model_validator(mode="after")
    def validate_integrity(self) -> "ToolResponsePayload":
        validate_tool_response_payload_integrity(self)
        return self

    @field_serializer("content", "metadata")
    def serialize_mutable_fields(self, value: Any) -> Any:
        return _thaw_json(value)


def validate_tool_response_payload_integrity(
    payload: ToolResponsePayload,
) -> ToolResponsePayload:
    required = {
        "payload_id": payload.payload_id,
        "source_result_id": payload.source_result_id,
        "invocation_id": payload.invocation_id,
        "execution_id": payload.execution_id,
        "tool_call_id": payload.tool_call_id,
        "logical_capability_id": payload.logical_capability_id,
        "content_digest": payload.content_digest,
    }
    for name, value in required.items():
        if not str(value).strip():
            raise ValueError(f"{name} must be non-empty")

    if payload.source_commit_state != COMMITTED_RESULT_STATE:
        raise ValueError("Only COMMITTED tool results may create ToolResponsePayload.")

    _validate_frozen_json(payload.content, path="$.content")
    _validate_frozen_json(payload.metadata, path="$.metadata")
    canonical = canonical_payload_bytes(_thaw_json(payload.content))
    canonical_payload_bytes(_thaw_json(payload.metadata))
    expected_digest = hashlib.sha256(canonical).hexdigest()
    if payload.content_digest != expected_digest:
        raise ValueError("ToolResponsePayload content_digest does not match content.")

    if payload.canonical_bytes != len(canonical):
        raise ValueError("ToolResponsePayload canonical_bytes does not match content.")

    expected_payload_id = tool_response_payload_id(
        source_result_id=payload.source_result_id,
        invocation_id=payload.invocation_id,
        execution_id=payload.execution_id,
        tool_call_id=payload.tool_call_id,
        logical_capability_id=payload.logical_capability_id,
        content_digest=expected_digest,
        payload_schema_version=payload.payload_schema_version,
    )
    if payload.payload_id != expected_payload_id:
        raise ValueError("ToolResponsePayload payload_id does not match derived identity.")

    return payload


def create_tool_response_payload(
    *,
    source_result_id: str,
    invocation_id: str,
    execution_id: str,
    tool_call_id: str,
    logical_capability_id: str,
    content: Any,
    source_commit_state: str,
    owner_user_id: str | None = None,
    session_id: str | None = None,
    content_type: str = "application/json",
    metadata: Mapping[str, Any] | None = None,
    payload_schema_version: int = TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION,
) -> ToolResponsePayload:
    """Create a dormant payload record from an already-committed tool result."""
    canonical = canonical_payload_bytes(content)
    digest = hashlib.sha256(canonical).hexdigest()
    payload_id = tool_response_payload_id(
        source_result_id=source_result_id,
        invocation_id=invocation_id,
        execution_id=execution_id,
        tool_call_id=tool_call_id,
        logical_capability_id=logical_capability_id,
        content_digest=digest,
        payload_schema_version=payload_schema_version,
    )
    return ToolResponsePayload(
        payload_id=payload_id,
        source_result_id=source_result_id,
        invocation_id=invocation_id,
        execution_id=execution_id,
        tool_call_id=tool_call_id,
        logical_capability_id=logical_capability_id,
        source_commit_state=source_commit_state,
        payload_schema_version=payload_schema_version,
        content_digest=digest,
        canonical_bytes=len(canonical),
        content=content,
        content_type=content_type,
        owner_user_id=owner_user_id,
        session_id=session_id,
        metadata=dict(metadata or {}),
    )


class ToolResponsePayloadRepository(Protocol):
    async def put(self, payload: ToolResponsePayload) -> ToolResponsePayload:
        ...

    async def get(self, payload_id: str) -> ToolResponsePayload | None:
        ...

    async def get_by_source_result(
        self, source_result_id: str
    ) -> ToolResponsePayload | None:
        ...


def _same_immutable_payload(
    left: ToolResponsePayload,
    right: ToolResponsePayload,
) -> bool:
    excluded = {"created_at"}
    return left.model_dump(exclude=excluded) == right.model_dump(exclude=excluded)


class InMemoryToolResponsePayloadRepository:
    """Process-local reference repository; not durable CTX storage authority."""

    def __init__(self) -> None:
        self._by_id: dict[str, ToolResponsePayload] = {}
        self._source_to_id: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def put(self, payload: ToolResponsePayload) -> ToolResponsePayload:
        validate_tool_response_payload_integrity(payload)

        async with self._lock:
            existing_id = self._source_to_id.get(payload.source_result_id)
            if existing_id is not None and existing_id != payload.payload_id:
                raise ToolResponsePayloadConflictError(
                    "source_result_id already maps to a different immutable payload"
                )

            existing = self._by_id.get(payload.payload_id)
            if existing is not None:
                if not _same_immutable_payload(existing, payload):
                    raise ToolResponsePayloadConflictError(
                        "payload_id already exists with conflicting immutable content"
                    )
                return existing

            self._by_id[payload.payload_id] = payload
            self._source_to_id[payload.source_result_id] = payload.payload_id
            return payload

    async def get(self, payload_id: str) -> ToolResponsePayload | None:
        return self._by_id.get(payload_id)

    async def get_by_source_result(
        self, source_result_id: str
    ) -> ToolResponsePayload | None:
        payload_id = self._source_to_id.get(source_result_id)
        if payload_id is None:
            return None
        return self._by_id.get(payload_id)
