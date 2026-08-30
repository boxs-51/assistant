import io
import httpx
from typing import Dict, Any
from .base import BaseExecutionHandler

class FileOperationHandler(BaseExecutionHandler):
    """Xử lý các tác vụ quản lý tập tin (List, Upload, Metadata, Download, Delete)."""

    async def execute(
        self, payload: Dict[str, Any], http_client: httpx.AsyncClient
    ) -> Any:
        action = payload.get("action")
        provider_name = payload.get("provider_name")

        provider = self.providers.get(provider_name)
        if not provider:
            raise KeyError(f"Provider '{provider_name}' not found.")

        timeout = self.timeout

        if action == "list":
            return await provider.files.list_files(
                http_client=http_client,
                timeout=timeout,
                page_size=payload.get("page_size"),
                page_token=payload.get("page_token")
            )
        elif action == "upload":
            return await provider.files.upload_file(
                http_client=http_client,
                timeout=timeout,
                file_stream=io.BytesIO(payload.get("file_bytes")),
                file_size=payload.get("file_size"),
                mime_type=payload.get("mime_type"),
                display_name=payload.get("display_name")
            )
        elif action == "get_metadata":
            return await provider.files.get_file(
                http_client=http_client,
                timeout=timeout,
                file_name=payload.get("file_id")
            )
        elif action == "download":
            meta = await provider.files.get_file(
                http_client=http_client,
                timeout=timeout,
                file_name=payload.get("file_id")
            )

            # Safely extract attributes from dict or object
            get_val = lambda k, default=None: meta.get(k, default) if isinstance(meta, dict) else getattr(meta, k, default)

            uri = get_val("uri")
            mime_type = get_val("mime_type", "application/octet-stream")
            filename = get_val("filename") or get_val("display_name", "file")

            if not uri:
                raise ValueError("File does not expose a valid download URI.")
            
            file_bytes = await provider.files.download_file(
                http_client=http_client,
                timeout=timeout,
                file_id=payload.get("file_id"),
                uri=uri
            )
            return {"bytes": file_bytes, "mime_type": mime_type, "filename": filename}
        
        elif action == "delete":
            return await provider.files.delete_file(
                http_client=http_client,
                timeout=timeout,
                file_name=payload.get("file_id")
            )
        else:
            raise ValueError(f"Unsupported file action: {action}")