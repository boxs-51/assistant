from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Optional


@dataclass(frozen=True, slots=True)
class AssetDescriptor:
    asset_id: str
    owner_user_id: str
    filename: str
    mime_type: str
    size_bytes: Optional[int]
    sha256: Optional[str]
    state: str
    uri: str
    origin_type: str
    revision: int
    declared_mime_type: Optional[str] = None
    detected_mime_type: Optional[str] = None


@dataclass(frozen=True, slots=True)
class AssetContent:
    descriptor: AssetDescriptor
    stream: AsyncIterator[bytes]


@dataclass(frozen=True, slots=True)
class AssetReferenceDescriptor:
    reference_id: str
    asset_id: str
    reference_type: str
    created_at: datetime
    message_id: Optional[str] = None
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    content_part_index: Optional[int] = None
    turn_id: Optional[str] = None
    sequence: Optional[int] = None
    role: Optional[str] = None
    agent_tool_result_id: Optional[str] = None
    execution_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    invocation_id: Optional[str] = None
    capability_id: Optional[str] = None
    commit_state: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
