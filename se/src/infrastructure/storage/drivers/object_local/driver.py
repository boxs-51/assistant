from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import AsyncIterable, AsyncIterator, Optional

from ....config.schemas import DriverConfig
from ...interfaces.object import (
    ObjectStat,
    ObjectStorageDriver,
    ObjectWriteResult,
)


class LocalObjectStorageDriver(ObjectStorageDriver):
    """Filesystem-backed object storage for development, CI and local deploys."""

    backend_name = "object-local"

    def __init__(self, config: DriverConfig):
        self.config = config
        self.root = Path(config.options.get("root", "data/assets")).expanduser()
        self._root_resolved: Optional[Path] = None
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True)
        root = await asyncio.to_thread(self.root.resolve)
        tmp = root / ".tmp"
        await asyncio.to_thread(tmp.mkdir, parents=True, exist_ok=True)
        self._root_resolved = root
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False
        self._root_resolved = None

    def _require_connected(self) -> Path:
        if not self._connected or self._root_resolved is None:
            raise RuntimeError("LocalObjectStorageDriver is not connected")
        return self._root_resolved

    @staticmethod
    def _validated_parts(object_key: str) -> tuple[str, ...]:
        if not isinstance(object_key, str) or not object_key:
            raise ValueError("object_key must be a non-empty relative path")
        if "\\" in object_key or "\x00" in object_key:
            raise ValueError("object_key contains an unsafe path character")
        key = PurePosixPath(object_key)
        if key.is_absolute():
            raise ValueError("object_key must be relative")
        parts = tuple(key.parts)
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("object_key contains an unsafe path segment")
        return parts

    def _path_for(self, object_key: str) -> Path:
        root = self._require_connected()
        parts = self._validated_parts(object_key)
        candidate = root.joinpath(*parts).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("object_key escapes configured storage root") from exc
        return candidate

    async def put_stream(
        self,
        object_key: str,
        stream: AsyncIterable[bytes],
        *,
        content_length: Optional[int] = None,
        content_type: Optional[str] = None,
    ) -> ObjectWriteResult:
        del content_type
        target = self._path_for(object_key)
        root = self._require_connected()
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)

        tmp = root / ".tmp" / f"{uuid.uuid4().hex}.part"
        handle = await asyncio.to_thread(tmp.open, "wb")
        digest = hashlib.sha256()
        size = 0
        try:
            async for chunk in stream:
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise TypeError("object stream chunks must be bytes-like")
                payload = bytes(chunk)
                if not payload:
                    continue
                digest.update(payload)
                size += len(payload)
                await asyncio.to_thread(handle.write, payload)

            await asyncio.to_thread(handle.flush)
            await asyncio.to_thread(os.fsync, handle.fileno())
        except BaseException:
            try:
                await asyncio.to_thread(handle.close)
            finally:
                if tmp.exists():
                    await asyncio.to_thread(tmp.unlink)
            raise
        else:
            await asyncio.to_thread(handle.close)

        if content_length is not None and size != int(content_length):
            if tmp.exists():
                await asyncio.to_thread(tmp.unlink)
            raise ValueError(
                f"content_length mismatch: expected {content_length}, wrote {size}"
            )

        await asyncio.to_thread(os.replace, tmp, target)
        sha256 = digest.hexdigest()
        return ObjectWriteResult(
            object_key=object_key,
            size_bytes=size,
            sha256=sha256,
            etag=sha256,
        )

    async def open_stream(
        self,
        object_key: str,
        *,
        byte_range: Optional[tuple[int, Optional[int]]] = None,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")

        path = self._path_for(object_key)
        info = await asyncio.to_thread(path.stat)
        start = 0
        end: Optional[int] = None
        if byte_range is not None:
            start, end = byte_range
            if start < 0 or (end is not None and end < start):
                raise ValueError("invalid byte range")
            if start >= info.st_size:
                raise ValueError("byte range starts beyond end of object")
            if end is not None:
                end = min(end, info.st_size - 1)

        async def iterator() -> AsyncIterator[bytes]:
            handle = await asyncio.to_thread(path.open, "rb")
            try:
                if start:
                    await asyncio.to_thread(handle.seek, start)
                remaining = (
                    None
                    if end is None
                    else end - start + 1
                )
                while remaining is None or remaining > 0:
                    read_size = (
                        chunk_size
                        if remaining is None
                        else min(chunk_size, remaining)
                    )
                    payload = await asyncio.to_thread(handle.read, read_size)
                    if not payload:
                        break
                    if remaining is not None:
                        remaining -= len(payload)
                    yield payload
            finally:
                await asyncio.to_thread(handle.close)

        return iterator()

    async def stat(self, object_key: str) -> ObjectStat:
        path = self._path_for(object_key)
        info = await asyncio.to_thread(path.stat)
        return ObjectStat(
            object_key=object_key,
            size_bytes=info.st_size,
            modified_at=datetime.fromtimestamp(
                info.st_mtime,
                tz=timezone.utc,
            ),
        )

    async def exists(self, object_key: str) -> bool:
        path = self._path_for(object_key)
        return await asyncio.to_thread(path.is_file)

    async def delete(self, object_key: str) -> None:
        path = self._path_for(object_key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return

    async def presign_get(
        self,
        object_key: str,
        *,
        expires_seconds: int = 300,
    ) -> Optional[str]:
        del expires_seconds
        self._path_for(object_key)
        return None
