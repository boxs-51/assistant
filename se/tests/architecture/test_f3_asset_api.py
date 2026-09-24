from __future__ import annotations

from types import SimpleNamespace
import tempfile

import httpx
import pytest
import starlette.formparsers as formparsers
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from se.src.application.assets import (
    AssetAccessDeniedError,
    AssetContent,
    AssetDescriptor,
    AssetStateError,
)
from se.src.domain.schemas.identity import Identity
from se.src.transport.gateway.api.v1 import assets_router
from se.src.transport.gateway.authentication.dependency import (
    get_current_identity,
)
from se.src.transport.gateway.dependencies import get_container
from se.src.transport.gateway.middleware.asset_upload_limit import (
    AssetUploadBodyLimitMiddleware,
)


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
    )


async def _stream_bytes(payload: bytes):
    yield payload


class _FakeAssetService:
    def __init__(self):
        self.upload_chunks: list[int] = []
        self.ingest_calls = 0
        self.last_range = None
        self.list_kwargs = None
        self.get_kwargs = None

    async def ingest_stream(self, **kwargs):
        self.ingest_calls += 1
        async for chunk in kwargs["stream"]:
            self.upload_chunks.append(len(chunk))
        return _descriptor()

    async def list_assets(self, **kwargs):
        self.list_kwargs = kwargs
        return (_descriptor(),), 1

    async def get_asset(self, **kwargs):
        self.get_kwargs = kwargs
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


class _ForeignAssetService(_FakeAssetService):
    async def get_asset(self, **kwargs):
        raise AssetAccessDeniedError("asset-f3")


class _NonReadyAssetService(_FakeAssetService):
    async def get_asset(self, **kwargs):
        raise AssetStateError("Asset asset-f3 is not READY")


def _identity() -> Identity:
    return Identity(
        user_id="user-a",
        organization_id="org-a",
        auth_type="guest",
    )


def _container(service, *, max_upload_bytes=1024):
    return SimpleNamespace(
        asset_service=service,
        config=SimpleNamespace(
            assets=SimpleNamespace(
                max_upload_bytes=max_upload_bytes,
                upload_chunk_bytes=4,
                list_default_limit=25,
                list_max_limit=100,
            )
        ),
    )


def _app(
    service,
    *,
    max_upload_bytes: int = 1024,
    multipart_overhead_bytes: int = 64 * 1024,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        AssetUploadBodyLimitMiddleware,
        max_upload_bytes=max_upload_bytes,
        multipart_overhead_bytes=multipart_overhead_bytes,
    )
    app.include_router(assets_router.router)
    container = _container(
        service,
        max_upload_bytes=max_upload_bytes,
    )
    app.dependency_overrides[get_current_identity] = _identity
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
            files={
                "file": (
                    "image.png",
                    b"0123456789",
                    "image/png",
                )
            },
        )
    assert response.status_code == 201
    assert response.json()["asset_id"] == "asset-f3"
    assert service.upload_chunks == [4, 4, 2]


class _ClosableUpload:
    filename = "large.bin"
    content_type = "application/octet-stream"
    size = 11

    def __init__(self):
        self.closed = False

    async def read(self, size):
        return b"x" * min(size, self.size)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_f3_declared_oversize_closes_upload_before_413():
    upload = _ClosableUpload()
    with pytest.raises(HTTPException) as caught:
        await assets_router.upload_asset(
            file=upload,
            identity=_identity(),
            container=_container(
                _FakeAssetService(),
                max_upload_bytes=10,
            ),
        )
    assert caught.value.status_code == 413
    assert upload.closed is True




@pytest.mark.asyncio
async def test_f3_declared_oversize_is_rejected_before_service_invocation():
    service = _FakeAssetService()
    app = _app(
        service,
        max_upload_bytes=64,
        multipart_overhead_bytes=128,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/assets",
            files={
                "file": (
                    "too-large.bin",
                    b"x" * 512,
                    "application/octet-stream",
                )
            },
        )
    assert response.status_code == 413
    assert service.ingest_calls == 0
    assert service.upload_chunks == []


@pytest.mark.asyncio
async def test_f3_chunked_oversize_is_bounded_and_closes_spool(monkeypatch):
    service = _FakeAssetService()
    app = _app(
        service,
        max_upload_bytes=128,
        multipart_overhead_bytes=256,
    )
    created_files = []
    real_spooled = tempfile.SpooledTemporaryFile

    def tracking_spooled(*args, **kwargs):
        handle = real_spooled(*args, **kwargs)
        created_files.append(handle)
        return handle

    monkeypatch.setattr(
        formparsers,
        "SpooledTemporaryFile",
        tracking_spooled,
    )

    boundary = "cas-r0-boundary"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; '
        'filename="chunked.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    payload = b"z" * 700

    async def chunked_body():
        # First chunk stays under the raw ceiling and lets Starlette create
        # the UploadFile/SpooledTemporaryFile.
        yield prefix + payload[:180]
        # This chunk crosses the raw ingress ceiling.
        yield payload[180:500]
        yield payload[500:] + suffix

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/assets",
            content=chunked_body(),
            headers={
                "Content-Type": (
                    f"multipart/form-data; boundary={boundary}"
                )
            },
        )

    assert response.status_code == 413
    assert service.ingest_calls == 0
    assert created_files
    assert all(handle.closed for handle in created_files)


@pytest.mark.asyncio
async def test_f3_normal_upload_passes_preparse_ingress_bound():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(
        app=_app(
            service,
            max_upload_bytes=1024,
            multipart_overhead_bytes=512,
        )
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/assets",
            files={
                "file": (
                    "ok.bin",
                    b"normal-upload",
                    "application/octet-stream",
                )
            },
        )
    assert response.status_code == 201
    assert service.ingest_calls == 1




@pytest.mark.asyncio
async def test_f3_underreported_content_length_still_loses_to_receive_counter():
    service = _FakeAssetService()
    app = _app(
        service,
        max_upload_bytes=64,
        multipart_overhead_bytes=128,
    )
    boundary = "cas-r0-lying-length"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; '
        'filename="lying.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")

    async def body():
        yield prefix + (b"x" * 96)
        yield b"y" * 256
        yield suffix

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/assets",
            content=body(),
            headers={
                "Content-Type": (
                    f"multipart/form-data; boundary={boundary}"
                ),
                "Content-Length": "32",
            },
        )

    assert response.status_code == 413
    assert service.ingest_calls == 0


@pytest.mark.asyncio
async def test_f3_cross_origin_oversize_retains_cors_header():
    service = _FakeAssetService()
    app = FastAPI()
    app.add_middleware(
        AssetUploadBodyLimitMiddleware,
        max_upload_bytes=64,
        multipart_overhead_bytes=128,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(assets_router.router)
    container = _container(service, max_upload_bytes=64)
    app.dependency_overrides[get_current_identity] = _identity
    app.dependency_overrides[get_container] = lambda: container

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/assets",
            files={
                "file": (
                    "cors-too-large.bin",
                    b"x" * 512,
                    "application/octet-stream",
                )
            },
            headers={"Origin": "https://example.test"},
        )

    assert response.status_code == 413
    assert "access-control-allow-origin" in response.headers
    assert service.ingest_calls == 0


@pytest.mark.asyncio
async def test_f3_unrelated_post_is_not_subject_to_asset_body_limit():
    app = FastAPI()
    app.add_middleware(
        AssetUploadBodyLimitMiddleware,
        max_upload_bytes=16,
        multipart_overhead_bytes=16,
    )

    @app.post("/unrelated")
    async def unrelated():
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/unrelated",
            content=b"x" * 4096,
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_f3_list_is_bounded_and_has_no_lifecycle_state_filter():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/assets?limit=10&offset=2")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert service.list_kwargs == {
        "owner_user_id": "user-a",
        "limit": 10,
        "offset": 2,
    }


@pytest.mark.asyncio
async def test_f3_list_rejects_limit_above_configured_maximum():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/assets?limit=101")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_f3_range_download_returns_hardened_partial_contract():
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
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith(
        "attachment;"
    )
    assert response.headers["content-security-policy"] == "sandbox"
    assert service.last_range == (2, 5)


@pytest.mark.asyncio
async def test_f3_unsatisfiable_range_is_416_with_size():
    transport = httpx.ASGITransport(app=_app(_FakeAssetService()))
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
async def test_f3_foreign_asset_is_hidden_as_not_found():
    transport = httpx.ASGITransport(app=_app(_ForeignAssetService()))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/assets/asset-f3")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_f3_non_ready_metadata_is_not_exposed():
    transport = httpx.ASGITransport(app=_app(_NonReadyAssetService()))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/assets/asset-f3")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_f3_metadata_uses_canonical_ready_read_contract():
    service = _FakeAssetService()
    transport = httpx.ASGITransport(app=_app(service))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/assets/asset-f3")
    assert response.status_code == 200
    assert response.json()["uri"] == "asset://asset-f3"
    assert service.get_kwargs == {
        "owner_user_id": "user-a",
        "asset_id": "asset-f3",
    }


def test_f3_range_parser_supports_open_and_suffix_ranges():
    assert assets_router._parse_range_header(
        "bytes=2-5",
        10,
    ) == (2, 5)
    assert assets_router._parse_range_header(
        "bytes=6-",
        10,
    ) == (6, 9)
    assert assets_router._parse_range_header(
        "bytes=-3",
        10,
    ) == (7, 9)
    assert assets_router._parse_range_header(
        "bytes=9-99",
        10,
    ) == (9, 9)

    with pytest.raises(ValueError):
        assets_router._parse_range_header(
            "bytes=0-1,4-5",
            10,
        )
    with pytest.raises(ValueError):
        assets_router._parse_range_header(
            "items=0-1",
            10,
        )


def test_f3_router_surface_excludes_delete_references_and_v1_files():
    paths = {route.path for route in assets_router.router.routes}
    assert paths == {
        "/v1/assets",
        "/v1/assets/{asset_id}",
        "/v1/assets/{asset_id}/content",
    }
    assert "/v1/assets/{asset_id}/references" not in paths
    assert all(
        "DELETE" not in (route.methods or set())
        for route in assets_router.router.routes
    )
    assert all(
        not path.startswith("/v1/files")
        for path in paths
    )
