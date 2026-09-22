from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from se.src.application.assets import (
    AssetAccessDeniedError,
    AssetContent,
    AssetDescriptor,
    AssetReferenceDescriptor,
)
from se.src.domain.schemas.identity import Identity
from se.src.transport.gateway.api.v1 import assets_router
from se.src.transport.gateway.authentication.dependency import get_current_identity
from se.src.transport.gateway.dependencies import get_container


def _descriptor(*, state: str = "READY") -> AssetDescriptor:
    return AssetDescriptor(
        asset_id="asset-f3",
        owner_user_id="user-a",
        filename="ảnh demo.png",
        mime_type="image/png",
        size_bytes=10,
        sha256="a" * 64,
        state=state,
        uri="asset://asset-f3",
        origin_type="USER_UPLOAD",
        revision=1,
        declared_mime_type="image/png",
        detected_mime_type="image/png",
    )


async def _stream_bytes(payload: bytes):
    yield payload


class _FakeAssetService:
    def __init__(self):
        self.upload_chunks: list[int] = []
        self.last_range = None

    async def ingest_stream(self, **kwargs):
        async for chunk in kwargs["stream"]:
            self.upload_chunks.append(len(chunk))
        return _descriptor()

    async def list_assets(self, **kwargs):
        return (_descriptor(),), 1

    async def get_asset(self, **kwargs):
        return _descriptor()

    async def open_content(self, **kwargs):
        self.last_range = kwargs.get("byte_range")
        payload = b"0123456789"
        if self.last_range is not None:
            start, end = self.last_range
            payload = payload[start:end + 1]
        return AssetContent(
            descriptor=_descriptor(),
            stream=_stream_bytes(payload),
        )

    async def list_references(self, **kwargs):
        return (
            AssetReferenceDescriptor(
                reference_id="ref-f3",
                asset_id="asset-f3",
                reference_type="MESSAGE_CONTENT",
                created_at=datetime.now(timezone.utc),
                message_id="message-f3",
                session_id="session-f3",
                content_part_index=2,
                turn_id="turn-f3",
                sequence=7,
                role="user",
                metadata={},
            ),
        )

    async def delete_asset(self, **kwargs):
        return _descriptor(state="DELETING")


class _ForeignAssetService(_FakeAssetService):
    async def get_asset(self, **kwargs):
        raise AssetAccessDeniedError("asset-f3")


def _app(service) -> FastAPI:
    app = FastAPI()
    app.include_router(assets_router.router)
    container = SimpleNamespace(
        asset_service=service,
        config=SimpleNamespace(
            assets=SimpleNamespace(
                max_upload_bytes=1024,
                upload_chunk_bytes=4,
                list_default_limit=25,
                list_max_limit=100,
            )
        ),
    )
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id="user-a",
        organization_id="org-a",
        auth_type="guest",
    )
    app.dependency_overrides[get_container] = lambda: container
    return app


@pytest.mark.asyncio
async def test_f3_upload_streams_uploadfile_in_chunks_without_full_buffering():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/assets",
            files={"file": ("image.png", b"0123456789", "image/png")},
        )
    assert response.status_code == 201
    assert response.json()["asset_id"] == "asset-f3"
    assert service.upload_chunks == [4, 4, 2]


@pytest.mark.asyncio
async def test_f3_range_download_returns_partial_content_contract():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/v1/assets/asset-f3/content",
            headers={"Range": "bytes=2-5"},
        )
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["content-length"] == "4"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["etag"] == '"' + ("a" * 64) + '"'
    assert service.last_range == (2, 5)


@pytest.mark.asyncio
async def test_f3_unsatisfiable_range_is_416_with_size():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/v1/assets/asset-f3/content",
            headers={"Range": "bytes=50-60"},
        )
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"


@pytest.mark.asyncio
async def test_f3_list_metadata_references_and_logical_delete():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        listing = await client.get("/v1/assets")
        metadata = await client.get("/v1/assets/asset-f3")
        references = await client.get("/v1/assets/asset-f3/references")
        deleted = await client.delete("/v1/assets/asset-f3")

    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["asset_id"] == "asset-f3"
    assert metadata.status_code == 200
    assert metadata.json()["uri"] == "asset://asset-f3"
    assert references.status_code == 200
    assert references.json()["references"][0]["content_part_index"] == 2
    assert deleted.status_code == 202
    assert deleted.json()["state"] == "DELETING"


@pytest.mark.asyncio
async def test_f3_foreign_asset_is_hidden_as_not_found():
    service = _ForeignAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/assets/asset-f3")
    assert response.status_code == 404


def test_f3_range_parser_supports_open_and_suffix_ranges():
    assert assets_router._parse_range_header("bytes=2-5", 10) == (2, 5)
    assert assets_router._parse_range_header("bytes=6-", 10) == (6, 9)
    assert assets_router._parse_range_header("bytes=-3", 10) == (7, 9)
    assert assets_router._parse_range_header("bytes=9-99", 10) == (9, 9)

    with pytest.raises(ValueError):
        assets_router._parse_range_header("bytes=0-1,4-5", 10)
    with pytest.raises(ValueError):
        assets_router._parse_range_header("items=0-1", 10)


def test_f3_router_surface_is_separate_from_legacy_provider_files():
    paths = {route.path for route in assets_router.router.routes}
    assert "/v1/assets" in paths
    assert "/v1/assets/{asset_id}" in paths
    assert "/v1/assets/{asset_id}/content" in paths
    assert "/v1/assets/{asset_id}/references" in paths
    assert all(not path.startswith("/v1/files") for path in paths)
