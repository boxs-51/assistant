from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field


TOOL_RESPONSE_PAYLOAD_IDENTITY_DOMAIN = "ctx-tool-response-payload-v1"
TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION = 1
COMMITTED_RESULT_STATE = "COMMITTED"


class ToolResponsePayloadConflictError(RuntimeError):
    """Raised when one immutable source identity is reused inconsistently."""


def canonical_payload_bytes(value: Any) -> bytes:
    """Return the canonical JSON representation used by CTX-F1 identity."""
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("ToolResponsePayload content must be canonical JSON.") from exc
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

    model_config = ConfigDict(extra="forbid", frozen=True)

    payload_id: str
    source_result_id: str
    invocation_id: str
    execution_id: str
    tool_call_id: str
    logical_capability_id: str
    payload_schema_version: int = TOOL_RESPONSE_PAYLOAD_SCHEMA_VERSION
    content_digest: str
    canonical_bytes: int = Field(ge=0)
    content_type: str = "application/json"
    owner_user_id: str | None = None
    session_id: str | None = None
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
    if source_commit_state != COMMITTED_RESULT_STATE:
        raise ValueError("Only COMMITTED tool results may create ToolResponsePayload.")

    required = {
        "source_result_id": source_result_id,
        "invocation_id": invocation_id,
        "execution_id": execution_id,
        "tool_call_id": tool_call_id,
        "logical_capability_id": logical_capability_id,
    }
    for name, value in required.items():
        if not str(value).strip():
            raise ValueError(f"{name} must be non-empty")

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
        payload_schema_version=payload_schema_version,
        content_digest=digest,
        canonical_bytes=len(canonical),
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
