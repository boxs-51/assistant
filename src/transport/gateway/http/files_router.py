# src/gateway/http/files_router.py
import io
import uuid
import asyncio
import structlog
from typing import Optional, Literal, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query, UploadFile, File
from fastapi.responses import StreamingResponse, Response

from ..authentication.dependency import get_current_identity
from ....domain.schemas.identity import Identity
from ....domain.schemas.event import BaseEvent
from ....infrastructure.config import settings

router = APIRouter(prefix="/v1/files", tags=["Files"])
logger = structlog.get_logger(__name__)

async def _dispatch_file_event(event_bus, payload: dict) -> Any:
    session_id = str(uuid.uuid4())
    future = asyncio.get_running_loop().create_future()

    async def _on_success(evt: BaseEvent):
        if evt.session_id == session_id and not future.done():
            future.set_result(evt.payload.get("result"))

    async def _on_failure(evt: BaseEvent):
        if evt.session_id == session_id and not future.done():
            future.set_exception(HTTPException(
                status_code=evt.payload.get("status_code", 500),
                detail=evt.payload.get("error", "File operation failed.")
            ))

    event_bus.subscribe("provider.file.responded", _on_success)
    event_bus.subscribe("provider.failed", _on_failure)

    await event_bus.publish(BaseEvent(
        event_name="provider.file.execute",
        session_id=session_id,
        payload=payload
    ))

    try:
        return await asyncio.wait_for(future, timeout=settings.provider.timeout)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="File operation timed out.")


@router.get("/")
async def list_files_proxy(
    request: Request,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    page_size: Optional[int] = Query(None, alias="page_size"),
    page_token: Optional[str] = Query(None, alias="page_token"),
    identity: Identity = Depends(get_current_identity)
):
    structlog.contextvars.bind_contextvars(provider_name=provider_name)
    return await _dispatch_file_event(request.app.state.event_bus, {
        "action": "list",
        "provider_name": provider_name,
        "page_size": page_size,
        "page_token": page_token
    })


@router.post("/")
async def upload_file_proxy(
    request: Request,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    display_name: Optional[str] = Query(None, description="Tên hiển thị tùy chọn"),
    file: UploadFile = File(..., description="Tệp tin cần tải lên"),
    identity: Identity = Depends(get_current_identity)
):
    structlog.contextvars.bind_contextvars(provider_name=provider_name)
    content = await file.read()
    return await _dispatch_file_event(request.app.state.event_bus, {
        "action": "upload",
        "provider_name": provider_name,
        "file_bytes": content,
        "file_size": file.size,
        "mime_type": file.content_type or "application/octet-stream",
        "display_name": display_name or file.filename
    })


@router.get("/{file_id:path}")
async def get_or_download_file_proxy(
    request: Request,
    file_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    action: Literal["metadata", "download"] = Query("metadata", description="Hành động: lấy thông tin hoặc tải file"),
    identity: Identity = Depends(get_current_identity)
):
    structlog.contextvars.bind_contextvars(file_id=file_id, provider_name=provider_name)
    
    if action == "metadata":
        return await _dispatch_file_event(request.app.state.event_bus, {
            "action": "get_metadata",
            "provider_name": provider_name,
            "file_id": file_id
        })
    else:
        file_res = await _dispatch_file_event(request.app.state.event_bus, {
            "action": "download",
            "provider_name": provider_name,
            "file_id": file_id
        })
        return StreamingResponse(
            io.BytesIO(file_res["bytes"]),
            media_type=file_res["mime_type"] or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={file_res['filename'] or 'file'}"}
        )


@router.delete("/{file_id:path}")
async def delete_file_proxy(
    request: Request,
    file_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    identity: Identity = Depends(get_current_identity)
):
    structlog.contextvars.bind_contextvars(file_id=file_id, provider_name=provider_name)
    success = await _dispatch_file_event(request.app.state.event_bus, {
        "action": "delete",
        "provider_name": provider_name,
        "file_id": file_id
    })
    if success:
        return Response(status_code=204)
    raise HTTPException(status_code=400, detail=f"Provider failed to delete file '{file_id}'.")