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
                raise ValueError(f"JSON object keys must be strings at {path}")
            _validate_frozen_json(item, path=f"{path}.{key}")
        return
    if isinstance(value, tuple):
        for index, item in enumerate(value):
            _validate_frozen_json(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"ContextSourceRef contains a non-frozen JSON container at {path}")


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
    if "://" in lowered:
        raise ValueError("authority_id must be a native logical authority, not a URI/path/object key")
    if "\\" in authority_id or "/" in authority_id:
        raise ValueError("authority_id must be a native logical authority, not a URI/path/object key")
    if len(authority_id) >= 3 and authority_id[1:3] == ":/":
        raise ValueError("authority_id must be a native logical authority, not a URI/path/object key")


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

    if not isinstance(source_kind, ContextSourceKind):
        raise ValueError("source_kind must be a ContextSourceKind")

    if source_kind in _VERSIONED_SOURCE_KINDS:
        if authority_version is None:
            raise ValueError(f"{source_kind.value} requires authority_version")
        if isinstance(authority_version, bool) or not isinstance(authority_version, int):
            raise ValueError("authority_version must be an integer")
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


def _validate_optional_string_field(name: str, value: Any) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{name} must be a string or None")


def _validate_stored_context_source_ref_shape(ref: ContextSourceRef) -> None:
    declared_fields = set(type(ref).model_fields)
    stored_fields = set(ref.__dict__)
    extra_fields = stored_fields - declared_fields
    if ref.__pydantic_extra__:
        extra_fields.update(ref.__pydantic_extra__)
    if extra_fields:
        joined = ", ".join(sorted(extra_fields))
        raise ValueError(f"ContextSourceRef contains undeclared stored fields: {joined}")

    if not isinstance(ref.context_source_id, str) or not ref.context_source_id.strip():
        raise ValueError("context_source_id must be a non-empty string")
    if not isinstance(ref.source_kind, ContextSourceKind):
        raise ValueError("source_kind must be a ContextSourceKind")
    if not isinstance(ref.owner_user_id, str):
        raise ValueError("owner_user_id must be a string")
    if not isinstance(ref.authority_id, str):
        raise ValueError("authority_id must be a string")

    if ref.authority_version is not None:
        if isinstance(ref.authority_version, bool) or not isinstance(ref.authority_version, int):
            raise ValueError("authority_version must be an integer or None")

    _validate_optional_string_field("session_id", ref.session_id)
    _validate_optional_string_field("task_id", ref.task_id)
    _validate_optional_string_field("branch_id", ref.branch_id)
    _validate_optional_string_field("source_state", ref.source_state)

    if ref.source_created_at is not None and not isinstance(ref.source_created_at, datetime):
        raise ValueError("source_created_at must be a datetime or None")

    if not isinstance(ref.metadata, MappingProxyType):
        raise ValueError("metadata root must be a frozen JSON object")
    _validate_frozen_json(ref.metadata, path="$.metadata")


def validate_context_source_ref_integrity(
    ref: ContextSourceRef,
) -> ContextSourceRef:
    _validate_stored_context_source_ref_shape(ref)

    canonical_owner = _require_non_empty("owner_user_id", ref.owner_user_id)
    canonical_authority = _require_non_empty("authority_id", ref.authority_id)
    if ref.owner_user_id != canonical_owner:
        raise ValueError("owner_user_id must already be in canonical normalized form")
    if ref.authority_id != canonical_authority:
        raise ValueError("authority_id must already be in canonical normalized form")
    _reject_non_authority_locator(ref.authority_id)

    expected = context_source_id(
        source_kind=ref.source_kind,
        owner_user_id=ref.owner_user_id,
        authority_id=ref.authority_id,
        authority_version=ref.authority_version,
    )
    if ref.context_source_id != expected:
        raise ValueError("context_source_id does not match source authority identity")
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
