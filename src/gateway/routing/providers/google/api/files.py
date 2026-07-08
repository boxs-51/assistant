from typing import List ,Any
from pathlib import Path
import os

from ..converters.files.response import ResponseFiles 
from ...base import ApiType, BaseProvider
from ...base.interfaces.file import FileProvider
from ..file_extension import FileHelper

import structlog
logger = structlog.get_logger(__name__)

class GoogleFiles(FileProvider):
    def __init__(self, provider: BaseProvider):
        self.response = ResponseFiles()
        self.provider = provider

    async def download_file(self, **kwargs) -> bytes:
        """
        Lưu ý quan trọng: Google Gemini File API hiện tại KHÔNG hỗ trợ tải ngược 
        nội dung nhị phân (binary content) của file về local sau khi đã upload.
        URI được trả về chỉ dùng làm tham chiếu ngữ cảnh (context reference) cho Model.
        """
        file_uri = kwargs.get("uri")
        logger.error(
            "Gemini File API does not support downloading binary data back after upload", 
            provider=self.provider.name, 
            uri=file_uri
        )
        raise NotImplementedError(
            "Downloading binary contents via Gemini File API is not supported by Google. "
            "The file URI is purely intended for LLM context processing."
        )
        
    async def delete_file(self, **kwargs) -> bool:
        """Xóa một tệp khỏi hệ thống lưu trữ của Gemini File API."""
        raw_file_name = kwargs.get("file_name")
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        if not raw_file_name:
            logger.error("Missing required parameter 'file_name' in delete_file request")
            raise ValueError("Parameter 'file_name' is required.")
        if not http_client:
            logger.error("Missing required parameter 'http_client' in delete_file request")
            raise ValueError("Parameter 'http_client' is required.")

        clean_file_id = raw_file_name.replace("files/", "")

        logger.info(
            "Initiating file deletion request to Gemini",
            provider=self.provider.name,
            requested_file=raw_file_name,
            resolved_id=clean_file_id
        )

        try:
            # Gửi request DELETE tới v1beta/files/{clean_file_id}
            response = await self.provider.send(
                client=http_client,
                method="DELETE",
                api_type=ApiType.FILES,
                model=clean_file_id,
                timeout=timeout
            )

            status_code = getattr(response, "status_code", None) or getattr(response, "status", 200)
            
            # Gemini trả về 200 OK và body rỗng khi xóa thành công
            # Thêm trường hợp nếu file không tồn tại (404), ta cũng coi như xóa thành công (Idempotent)
            if status_code in [200, 204]:
                logger.info("Successfully deleted file from Gemini", provider=self.provider.name, file_id=clean_file_id)
                return True
            elif status_code == 404:
                logger.warning("File already deleted or expired on Gemini", provider=self.provider.name, file_id=clean_file_id)
                return True
                
            return False

        except Exception as e:
            # Nếu hàm self.send tự ném lỗi khi gặp 404, hãy xử lý tại đây để tránh sập luồng vô lý
            if "404" in str(e):
                logger.warning("File not found during delete (might be expired)", provider=self.provider.name, file_id=clean_file_id)
                return True
                
            logger.error("Unexpected error in delete_file", provider=self.provider.name, file_id=clean_file_id, error=str(e))
            raise e
        
    async def list_files(self, **kwargs) -> List[Any]:
        """Lấy danh sách tất cả các tệp đã tải lên Gemini File API."""
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")
        page_size = kwargs.get("page_size")
        page_token = kwargs.get("page_token")

        if not http_client:
            logger.error("Missing required parameter 'http_client' in list_files request")
            raise ValueError("Parameter 'http_client' is required.")

        # Chỉ nạp tham số nếu client thực sự truyền vào (tránh None)
        params = {}
        if page_size is not None:
            params["pageSize"] = int(page_size)
        if page_token:
            params["pageToken"] = str(page_token)

        logger.info("Fetching files list from Gemini File API", provider=self.provider.name)

        try:
            response = await self.provider.send(
                client=http_client,
                method="GET",
                api_type=ApiType.FILES,
                params=params if params else None,
                timeout=timeout,
            )

            status_code = getattr(response, "status_code", None) or getattr(response, "status", 200)
            if status_code not in [200, 201]:
                logger.error("Failed to list files from Gemini", status_code=status_code)
                return []

            # Ánh xạ danh sách kết quả qua adapter
            return await self.response.adapt_file_list_response(response=response)

        except Exception as e:
            logger.error("Failed to fetch or parse files list from Gemini API", error=str(e), provider=self.provider.name)
            raise e
        
    async def get_file(self, **kwargs) -> Any:
        """Lấy thông tin metadata của một tệp cụ thể từ Gemini File API."""
        raw_file_name = kwargs.get("file_name")  # Có thể là "files/abc" hoặc "abc"
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        if not raw_file_name:
            logger.error("Missing required parameter 'file_name' in get_file request")
            raise ValueError("Parameter 'file_name' is required.")
        if not http_client:
            logger.error("Missing required parameter 'http_client' in get_file request")
            raise ValueError("Parameter 'http_client' is required.")

        # Chuẩn hóa tên file: loại bỏ tiền tố "files/" nếu có
        clean_file_id = raw_file_name.replace("files/", "")

        logger.info(
            "Fetching file metadata from Gemini",
            provider=self.provider.name,
            requested_file=raw_file_name,
            resolved_id=clean_file_id
        )

        try:
            # Gửi request GET tới v1beta/files/{clean_file_id}
            response = await self.provider.send(
                client=http_client,
                method="GET",
                api_type=ApiType.FILES,
                model=clean_file_id,  # Tận dụng tham số định tuyến id/name qua model/endpoint
                timeout=timeout
            )

            logger.info("Successfully fetched file metadata from Gemini", provider=self.provider.name, file_id=clean_file_id)
            
            # Sử dụng lại hàm adapt_file_upload_response vì cấu trúc JSON trả về giống nhau
            return await self.response.adapt_file_upload_response(response)

        except Exception as e:
            logger.error("Unexpected error in get_file", provider=self.provider.name, file_id=clean_file_id, error=str(e))
            raise e
        
    async def upload_file(self, **kwargs) -> Any:
        """
        Tải tệp kích thước lớn lên Gemini File API bằng cơ chế Resumable Upload
        để không bị tràn bộ nhớ (RAM) và bypass giới hạn 20MB inlineData.
        """
        file_path = kwargs.get("file_path")
        display_name = kwargs.get("display_name")
        http_client = kwargs.get("http_client")
        timeout = kwargs.get("timeout")

        if not file_path:
            logger.error("Missing required parameter 'file_path' in upload request")
            raise ValueError("Parameter 'file_path' is required.")

        if not http_client:
            logger.error("Missing required parameter 'http_client' in upload request")
            raise ValueError("Parameter 'http_client' is required.")

        # Chuyển đổi file_path thành đối tượng Path để thao tác dễ dàng
        path_obj = Path(file_path)
        if not path_obj.is_file():
            logger.error("File does not exist at path", file_path=str(file_path))
            raise FileNotFoundError(f"File not found at {file_path}")

        # 1. Xác định kích thước file và MIME type
        try:
            file_size = os.path.getsize(path_obj)
            mime_type = FileHelper.detect_mime_type(path_obj)
            resolved_display_name = display_name or path_obj.name
        except Exception as e:
            logger.error("Failed to detect file properties", file_path=str(path_obj), error=str(e))
            raise e

        # --- BƯỚC 1: KHỞI TẠO SESSION RESUMABLE UPLOAD ---
        # File metadata được gửi trong phần body
        file_metadata = {
            "file": {
                "displayName": resolved_display_name,
            }
        }

        # Bắt buộc thêm các Header định danh cấu trúc dữ liệu tải lên cho Google Resumable protocol
        init_headers = {
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": mime_type,
        }

        logger.info(
            "Initiating resumable upload session to Gemini",
            provider=self.provider.name,
            file_name=resolved_display_name,
            file_size_bytes=file_size,
            mime_type=mime_type
        )

        try:
            # Gửi request POST khởi tạo session upload (sử dụng ApiType.FILES)
            # Note: endpoint của resumable upload thường là v1beta/files?uploadType=resumable
            init_response = await self.provider.send(
                client=http_client,
                method="POST",
                api_type=ApiType.FILES,
                params={"uploadType": "resumable"}, # Thêm query param chỉ định kiểu resumable
                headers=init_headers,
                json=file_metadata,
                timeout=timeout,
            )

            # Trích xuất Upload URL đặc định từ Header 'Location'
            # Tùy thuộc vào việc hàm self.send của bạn trả về object httpx.Response hay dict:
            # Nếu là httpx/aiohttp Response: sử dụng init_response.headers.get("Location")
            upload_url = getattr(init_response, "headers", {}).get("Location")
            
            if not upload_url:
                # Fallback nếu hàm self.send của bạn parse json sẵn và đưa headers vào chỗ khác
                if isinstance(init_response, dict) and "upload_url" in init_response:
                    upload_url = init_response.get("upload_url")
                else:
                    raise ValueError("Could not find 'Location' header in Gemini response to start uploading data.")

            logger.info("Resumable upload session created successfully. Starting data transmission...")

            # --- BƯỚC 2: STREAM DỮ LIỆU BINARY LÊN UPLOAD URL ---
            # Đối với file lớn, mở file ở dạng 'rb' và truyền trực tiếp qua HTTP client.
            # Hầu hết các client như httpx, aiohttp hỗ trợ truyền một "file-like object" 
            # giúp tự động stream dữ liệu theo từng block mà không nạp toàn bộ file vào RAM cùng lúc.
            
            with open(path_obj, "rb") as file_stream:
                # Gửi PUT request trực tiếp lên URL được chỉ định riêng từ Google
                # Note: Do gửi lên URL tùy biến (Location), hàm self.send của bạn cần hỗ trợ 
                # ghi đè endpoint đầy đủ nếu truyền trực tiếp url hoặc chỉnh lại logic.
                # Ở đây giả định bạn có thể request qua http_client bất đồng bộ gốc hoặc chỉnh phương thức send:
                
                upload_response = await http_client.request(
                    method="PUT",
                    url=upload_url,
                    content=file_stream, # Stream dữ liệu thô
                    headers={
                        "Content-Length": str(file_size),
                        "Content-Type": mime_type
                    },
                    timeout=timeout if timeout else 300.0 # Tăng timeout đối với file dung lượng lớn
                )

            logger.info(
                "Successfully completed big file upload process to Gemini",
                provider=self.provider.name,
                status_code=upload_response.status_code
            )

            # 4. Ánh xạ dữ liệu trả về thông qua adapter
            return await self.response.adapt_file_upload_response(upload_response)

        except Exception as e:
            logger.error(
                "Unexpected error occurred during large file upload process",
                provider=self.provider.name,
                file_name=resolved_display_name,
                error=str(e)
            )
            raise e

