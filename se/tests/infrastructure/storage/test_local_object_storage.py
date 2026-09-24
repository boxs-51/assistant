from __future__ import annotations

import hashlib

import pytest

from se.src.infrastructure.config.schemas import DriverConfig
from se.src.infrastructure.storage.drivers.object_local.driver import (
    LocalObjectStorageDriver,
)


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


@pytest.mark.asyncio
async def test_f2_local_object_storage_streams_hashes_ranges_and_deletes(tmp_path):
    driver = LocalObjectStorageDriver(
        DriverConfig(
            enabled=True,
            required=True,
            options={"root": str(tmp_path / "objects")},
        )
    )
    await driver.connect()
    try:
        result = await driver.put_stream(
            "blobs/blob-1",
            _chunks(b"hello ", b"world"),
            content_length=11,
            content_type="text/plain",
        )
        assert result.size_bytes == 11
        assert result.sha256 == hashlib.sha256(b"hello world").hexdigest()
        assert await driver.exists("blobs/blob-1") is True

        stat = await driver.stat("blobs/blob-1")
        assert stat.size_bytes == 11

        stream = await driver.open_stream("blobs/blob-1")
        assert b"".join([chunk async for chunk in stream]) == b"hello world"

        partial = await driver.open_stream(
            "blobs/blob-1",
            byte_range=(6, 10),
            chunk_size=2,
        )
        assert b"".join([chunk async for chunk in partial]) == b"world"

        await driver.delete("blobs/blob-1")
        assert await driver.exists("blobs/blob-1") is False
        await driver.delete("blobs/blob-1")
    finally:
        await driver.disconnect()


@pytest.mark.asyncio
async def test_f2_local_object_storage_rejects_path_traversal_and_length_mismatch(
    tmp_path,
):
    driver = LocalObjectStorageDriver(
        DriverConfig(options={"root": str(tmp_path / "objects")})
    )
    await driver.connect()
    try:
        with pytest.raises(ValueError):
            await driver.put_stream("../escape", _chunks(b"x"))
        with pytest.raises(ValueError):
            await driver.put_stream("C:\\escape", _chunks(b"x"))
        with pytest.raises(ValueError):
            await driver.put_stream(
                "blobs/short",
                _chunks(b"abc"),
                content_length=4,
            )
        assert await driver.exists("blobs/short") is False
    finally:
        await driver.disconnect()
