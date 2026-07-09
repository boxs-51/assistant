from fastapi import APIRouter, Depends, HTTPException, status, Request, Query, UploadFile, File
from fastapi.responses import StreamingResponse, Response
from typing import Optional, Literal
import structlog
import io

from ..authentication.dependency import get_current_identity
from ..schemas.identity import Identity
from ..config import settings

router = APIRouter(prefix="/v1/files", tags=["Files"])
logger = structlog.get_logger(__name__)

@router.get("/")
async def list_files_proxy(
    request: Request,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    page_size: Optional[int] = Query(None, alias="page_size"),
    page_token: Optional[str] = Query(None, alias="page_token"),
    identity: Identity = Depends(get_current_identity)
):
    """Endpoint lấy danh sách các file có sẵn từ Provider."""
    structlog.contextvars.bind_contextvars(provider_name=provider_name)

    provider = request.app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        files_list = await provider.files.list_files(
            http_client=request.app.state.http_client,
            timeout=settings.provider.timeout,
            page_size=page_size,
            page_token=page_token
        )
        return files_list
    except Exception as e:
        logger.error("Failed to list files", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve files list.")

@router.post("/")
async def upload_file_proxy(
    request: Request,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    display_name: Optional[str] = Query(None, description="Tên hiển thị tùy chọn"),
    file: UploadFile = File(..., description="Tệp tin cần tải lên"),
    identity: Identity = Depends(get_current_identity)
):
    """Endpoint tải tệp tin lên hệ thống lưu trữ của Provider thông qua Stream."""
    structlog.contextvars.bind_contextvars(provider_name=provider_name)

    provider = request.app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        upload_result = await provider.files.upload_file(
            http_client=request.app.state.http_client,
            timeout=settings.provider.timeout,
            file_stream=file,
            file_size=file.size,
            mime_type=file.content_type or "application/octet-stream",
            display_name=display_name or file.filename
        )
        return upload_result
    except Exception as e:
        logger.error("Failed to upload file", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to upload file.")

@router.get("/{file_id:path}")
async def get_or_download_file_proxy(
    request: Request,
    file_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    action: Literal["metadata", "download"] = Query("metadata", description="Hành động: lấy thông tin hoặc tải file"),
    identity: Identity = Depends(get_current_identity)
):
    """Endpoint lấy thông tin chi tiết (metadata) HOẶC tải nội dung nhị phân của một file."""
    structlog.contextvars.bind_contextvars(file_id=file_id, provider_name=provider_name)

    provider = request.app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        file_metadata = await provider.files.get_file(
            http_client=request.app.state.http_client,
            timeout=settings.provider.timeout,
            file_name=file_id
        )

        if action == "metadata":
            return file_metadata
        
        elif action == "download":
            if not file_metadata.uri:
                raise HTTPException(status_code=400, detail="The requested file does not expose a valid download URI.")
            
            file_bytes = await provider.files.download_file(
                http_client=request.app.state.http_client,
                timeout=settings.provider.timeout,
                uri=file_metadata.uri
            )
            
            return StreamingResponse(
                io.BytesIO(file_bytes),
                media_type=file_metadata.mime_type or "application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={file_metadata.filename or 'file'}"}
            )

    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        logger.error("Error processing file request", action=action, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process file request for '{file_id}'.")

@router.delete("/{file_id:path}")
async def delete_file_proxy(
    request: Request,
    file_id: str,
    provider_name: str = Query(..., description="Tên nhà cung cấp"),
    identity: Identity = Depends(get_current_identity)
):
    """Endpoint để xóa một tệp cụ thể ra khỏi hệ thống của provider."""
    structlog.contextvars.bind_contextvars(file_id=file_id, provider_name=provider_name)

    provider = request.app.state.router.providers.get(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not found.")

    try:
        success = await provider.files.delete_file(
            http_client=request.app.state.http_client,
            timeout=settings.provider.timeout,
            file_name=file_id
        )
        if success:
            return Response(status_code=204)
        else:
            raise HTTPException(status_code=400, detail=f"Provider failed to delete file '{file_id}'.")
            
    except HTTPException as http_err:
        raise http_err
    except Exception as e:
        logger.error("Failed to delete file", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail=f"An error occurred while deleting file '{file_id}'.")

