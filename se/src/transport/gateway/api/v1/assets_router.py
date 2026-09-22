from __future__ import annotations

from dataclasses import asdict
from typing import NoReturn
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from .....application.assets import (
    AssetAccessDeniedError,
    AssetError,
    AssetFinalizeError,
    AssetInUseError,
    AssetMimeMismatchError,
    AssetNotFoundError,
    AssetStateError,
    AssetStorageError,
    AssetTooLargeError,
)
from .....application.container import ApplicationContainer
from .....domain.schemas.identity import Identity
from ...authentication.dependency import get_current_identity
from ...dependencies import get_container


router = APIRouter(prefix="/v1/assets", tags=["Assets"])


def _require_user_id(identity: Identity) -> str:
    if not identity.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user identity is required.",
        )
    return identity.user_id


def _asset_service(container: ApplicationContainer):
    service = getattr(container, "asset_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Central Asset Storage is unavailable.",
        )
    return service


def _raise_asset_http(error: Exception) -> NoReturn:
    if isinstance(error, (AssetNotFoundError, AssetAccessDeniedError)):
        # Do not disclose whether a foreign-owned asset exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found.",
        ) from error
    if isinstance(error, AssetTooLargeError):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(error),
        ) from error
    if isinstance(error, AssetMimeMismatchError):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(error),
        ) from error
    if isinstance(error, (AssetInUseError, AssetStateError)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    if isinstance(error, AssetStorageError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Asset storage backend is unavailable.",
        ) from error
    if isinstance(error, AssetFinalizeError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Asset persistence could not be finalized.",
        ) from error
    if isinstance(error, AssetError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Asset operation failed.",
        ) from error
    raise error


def _safe_filename(value: str | None) -> str:
    raw = (value or "asset.bin").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].replace("\r", "").replace("\n", "").strip()
    return name or "asset.bin"


def _content_disposition(filename: str) -> str:
    safe = _safe_filename(filename)
    ascii_name = safe.encode("ascii", "ignore").decode("ascii") or "asset.bin"
    ascii_name = ascii_name.replace('"', "")
    return (
        f'inline; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(safe, safe='')}"
    )


def _parse_range_header(
    value: str | None,
    size_bytes: int,
) -> tuple[int, int] | None:
    if value is None:
        return None
    if size_bytes < 0:
        raise ValueError("Asset size is invalid.")
    if not value.startswith("bytes="):
        raise ValueError("Only byte ranges are supported.")
    spec = value[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        raise ValueError("Exactly one byte range is supported.")
    start_raw, end_raw = spec.split("-", 1)

    if size_bytes == 0:
        raise ValueError("Empty assets do not have satisfiable byte ranges.")

    if not start_raw:
        try:
            suffix = int(end_raw)
        except ValueError as exc:
            raise ValueError("Invalid suffix byte range.") from exc
        if suffix <= 0:
            raise ValueError("Suffix byte range must be positive.")
        start = max(0, size_bytes - suffix)
        return start, size_bytes - 1

    try:
        start = int(start_raw)
    except ValueError as exc:
        raise ValueError("Invalid byte range start.") from exc
    if start < 0 or start >= size_bytes:
        raise ValueError("Byte range starts outside the asset.")

    if end_raw:
        try:
            end = int(end_raw)
        except ValueError as exc:
            raise ValueError("Invalid byte range end.") from exc
        if end < start:
            raise ValueError("Byte range end precedes start.")
        end = min(end, size_bytes - 1)
    else:
        end = size_bytes - 1
    return start, end


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_asset(
    file: UploadFile = File(...),
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    owner_user_id = _require_user_id(identity)
    service = _asset_service(container)
    settings = container.config.assets
    max_bytes = settings.max_upload_bytes
    chunk_bytes = settings.upload_chunk_bytes

    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload exceeds the {max_bytes}-byte asset limit.",
        )

    async def stream():
        while True:
            chunk = await file.read(chunk_bytes)
            if not chunk:
                break
            yield chunk

    try:
        descriptor = await service.ingest_stream(
            owner_user_id=owner_user_id,
            organization_id=identity.organization_id,
            filename=_safe_filename(file.filename),
            mime_type=file.content_type or "application/octet-stream",
            stream=stream(),
            content_length=file.size,
            max_bytes=max_bytes,
            origin_type="USER_UPLOAD",
        )
        return asdict(descriptor)
    except Exception as exc:
        _raise_asset_http(exc)
    finally:
        await file.close()


@router.get("")
async def list_assets(
    state_filter: list[str] | None = Query(None, alias="state"),
    limit: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    owner_user_id = _require_user_id(identity)
    service = _asset_service(container)
    settings = container.config.assets
    page_limit = limit or settings.list_default_limit
    if page_limit > settings.list_max_limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"limit cannot exceed {settings.list_max_limit}.",
        )
    try:
        items, total = await service.list_assets(
            owner_user_id=owner_user_id,
            states=tuple(state_filter) if state_filter else None,
            limit=page_limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        _raise_asset_http(exc)

    serialized = [asdict(item) for item in items]
    next_offset = offset + len(serialized)
    if next_offset >= total:
        next_offset = None
    return {
        "items": serialized,
        "total": total,
        "limit": page_limit,
        "offset": offset,
        "next_offset": next_offset,
    }


@router.get("/{asset_id}/references")
async def list_asset_references(
    asset_id: str,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    owner_user_id = _require_user_id(identity)
    service = _asset_service(container)
    try:
        references = await service.list_references(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
        )
        return {
            "asset_id": asset_id,
            "references": [asdict(item) for item in references],
        }
    except Exception as exc:
        _raise_asset_http(exc)


@router.get("/{asset_id}/content")
async def stream_asset_content(
    asset_id: str,
    range_header: str | None = Header(None, alias="Range"),
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    owner_user_id = _require_user_id(identity)
    service = _asset_service(container)
    try:
        descriptor = await service.get_asset(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
        )
    except Exception as exc:
        _raise_asset_http(exc)

    if descriptor.size_bytes is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset size is unavailable.",
        )

    try:
        byte_range = _parse_range_header(range_header, descriptor.size_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=str(exc),
            headers={"Content-Range": f"bytes */{descriptor.size_bytes}"},
        ) from exc

    try:
        content = await service.open_content(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            byte_range=byte_range,
        )
    except Exception as exc:
        _raise_asset_http(exc)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": _content_disposition(descriptor.filename),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if descriptor.sha256:
        headers["ETag"] = f'"{descriptor.sha256}"'

    response_status = status.HTTP_200_OK
    if byte_range is None:
        content_length = descriptor.size_bytes
    else:
        start, end = byte_range
        response_status = status.HTTP_206_PARTIAL_CONTENT
        content_length = end - start + 1
        headers["Content-Range"] = (
            f"bytes {start}-{end}/{descriptor.size_bytes}"
        )
    headers["Content-Length"] = str(content_length)

    return StreamingResponse(
        content.stream,
        status_code=response_status,
        media_type=descriptor.mime_type,
        headers=headers,
    )


@router.get("/{asset_id}")
async def get_asset_metadata(
    asset_id: str,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    owner_user_id = _require_user_id(identity)
    service = _asset_service(container)
    try:
        descriptor = await service.get_asset(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            allow_deleted=True,
        )
        return asdict(descriptor)
    except Exception as exc:
        _raise_asset_http(exc)


@router.delete("/{asset_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_asset(
    asset_id: str,
    identity: Identity = Depends(get_current_identity),
    container: ApplicationContainer = Depends(get_container),
):
    owner_user_id = _require_user_id(identity)
    service = _asset_service(container)
    try:
        descriptor = await service.delete_asset(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
        )
        return asdict(descriptor)
    except Exception as exc:
        _raise_asset_http(exc)
