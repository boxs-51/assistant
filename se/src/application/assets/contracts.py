from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Optional


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
