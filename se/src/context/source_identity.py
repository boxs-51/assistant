from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


CONTEXT_SOURCE_IDENTITY_DOMAIN = "ctx-context-source-v1"


class ContextSourceKind(StrEnum):
    SESSION = "SESSION"
    TASK = "TASK"
    BRANCH = "BRANCH"
    AGENT_TRANSCRIPT = "AGENT_TRANSCRIPT"
    ASSET = "ASSET"
    TOOL_RESPONSE_PAYLOAD = "TOOL_RESPONSE_PAYLOAD"


_VERSIONED_SOURCE_KINDS = {
    ContextSourceKind.TASK,
    ContextSourceKind.BRANCH,
    ContextSourceKind.AGENT_TRANSCRIPT,
}

_UNVERSIONED_SOURCE_KINDS = {
    ContextSourceKind.SESSION,
    ContextSourceKind.ASSET,
    ContextSourceKind.TOOL_RESPONSE_PAYLOAD,
}


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


def _validate_json(value: Any, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"non-finite number at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"JSON object keys must be strings at {path}")
            _validate_json(item, path=f"{path}.{key}")
        return
    raise ValueError(f"unsupported JSON value at {path}")


def _canonical_json_bytes(value: Any) -> bytes:
    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_non_empty(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _reject_non_authority_locator(authority_id: str) -> None:
    lowered = authority_id.lower()
    if lowered.startswith(("http://", "https://", "file://")):
        raise ValueError("authority_id must be a native logical authority, not a URL/path")
    if "\\" in authority_id or authority_id.startswith(("/", "./", "../")):
        raise ValueError("authority_id must be a native logical authority, not a URL/path")


def context_source_id(
    *,
    source_kind: ContextSourceKind,
    owner_user_id: str,
    authority_id: str,
    authority_version: int | None = None,
) -> str:
    owner_user_id = _require_non_empty("owner_user_id", owner_user_id)
    authority_id = _require_non_empty("authority_id", authority_id)
    _reject_non_authority_locator(authority_id)

    if source_kind in _VERSIONED_SOURCE_KINDS:
        if authority_version is None:
            raise ValueError(f"{source_kind.value} requires authority_version")
        if authority_version < 0:
            raise ValueError("authority_version must be >= 0")
    elif source_kind in _UNVERSIONED_SOURCE_KINDS:
        if authority_version is not None:
            raise ValueError(f"{source_kind.value} does not use authority_version")

    material = {
        "source_kind": source_kind.value,
        "owner_user_id": owner_user_id,
        "authority_id": authority_id,
    }
    if authority_version is not None:
        material["authority_version"] = authority_version

    return hashlib.sha256(
        CONTEXT_SOURCE_IDENTITY_DOMAIN.encode("utf-8")
        + b"\x00"
        + _canonical_json_bytes(material)
    ).hexdigest()


class ContextSourceRef(BaseModel):
    """Dormant CTX-F2A projection identity for one canonical source authority."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    context_source_id: str
    source_kind: ContextSourceKind
    authority_id: str
    authority_version: int | None = None
    owner_user_id: str
    session_id: str | None = None
    task_id: str | None = None
    branch_id: str | None = None
    source_created_at: datetime | None = None
    source_state: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict, validate_default=True)

    @field_validator("owner_user_id", "authority_id", mode="before")
    @classmethod
    def normalize_identity_strings(cls, value: Any, info) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{info.field_name} must be a string")
        normalized = _require_non_empty(info.field_name, value)
        if info.field_name == "authority_id":
            _reject_non_authority_locator(normalized)
        return normalized

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: Any) -> Any:
        _validate_json(value)
        return value

    @field_validator("metadata", mode="after")
    @classmethod
    def freeze_metadata(cls, value: dict[str, Any]) -> MappingProxyType:
        return _freeze_json(value)

    @model_validator(mode="after")
    def validate_identity(self) -> "ContextSourceRef":
        return validate_context_source_ref_integrity(self)

    @field_serializer("metadata")
    def serialize_metadata(self, value: Any) -> Any:
        return _thaw_json(value)


def validate_context_source_ref_integrity(
    ref: ContextSourceRef,
) -> ContextSourceRef:
    expected = context_source_id(
        source_kind=ref.source_kind,
        owner_user_id=ref.owner_user_id,
        authority_id=ref.authority_id,
        authority_version=ref.authority_version,
    )
    if ref.context_source_id != expected:
        raise ValueError("context_source_id does not match source authority identity")
    _validate_json(_thaw_json(ref.metadata))
    if not isinstance(ref.metadata, MappingProxyType):
        raise ValueError("ContextSourceRef metadata must be recursively frozen")
    return ref


def create_context_source_ref(
    *,
    source_kind: ContextSourceKind,
    authority_id: str,
    owner_user_id: str,
    authority_version: int | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    branch_id: str | None = None,
    source_created_at: datetime | None = None,
    source_state: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ContextSourceRef:
    payload = dict(metadata or {})
    source_id = context_source_id(
        source_kind=source_kind,
        owner_user_id=owner_user_id,
        authority_id=authority_id,
        authority_version=authority_version,
    )
    return ContextSourceRef(
        context_source_id=source_id,
        source_kind=source_kind,
        authority_id=authority_id,
        authority_version=authority_version,
        owner_user_id=owner_user_id,
        session_id=session_id,
        task_id=task_id,
        branch_id=branch_id,
        source_created_at=source_created_at,
        source_state=source_state,
        metadata=payload,
    )
