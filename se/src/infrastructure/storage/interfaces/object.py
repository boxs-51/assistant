from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterable, AsyncIterator, Optional


@dataclass(frozen=True, slots=True)
class ObjectWriteResult:
    object_key: str
    size_bytes: int
    sha256: str
    etag: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ObjectStat:
    object_key: str
    size_bytes: int
    etag: Optional[str] = None
    modified_at: Optional[datetime] = None


class ObjectStorageDriver(ABC):
    """Backend-neutral binary object storage contract.

    Application code must consume this interface rather than an SDK client.
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def put_stream(
        self,
        object_key: str,
        stream: AsyncIterable[bytes],
        *,
        content_length: Optional[int] = None,
        content_type: Optional[str] = None,
    ) -> ObjectWriteResult: ...

    @abstractmethod
    async def open_stream(
        self,
        object_key: str,
        *,
        byte_range: Optional[tuple[int, Optional[int]]] = None,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]: ...

    @abstractmethod
    async def stat(self, object_key: str) -> ObjectStat: ...

    @abstractmethod
    async def exists(self, object_key: str) -> bool: ...

    @abstractmethod
    async def delete(self, object_key: str) -> None: ...

    @abstractmethod
    async def presign_get(
        self,
        object_key: str,
        *,
        expires_seconds: int = 300,
    ) -> Optional[str]: ...
