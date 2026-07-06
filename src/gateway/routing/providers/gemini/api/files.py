from typing import List ,Any

from ..converters.files.response import FileResponse
from ...base import ApiType

import structlog
logger = structlog.get_logger(__name__)
class FileGemini():
    def __init__(self):
        self.response = FileResponse()


    async def download_file(self, **kwargs) -> bytes:
        """
        Lưu ý quan trọng: Google Gemini File API hiện tại KHÔNG hỗ trợ tải ngược 
        nội dung nhị phân (binary content) của file về local sau khi đã upload.
        URI được trả về chỉ dùng làm tham chiếu ngữ cảnh (context reference) cho Model.
        """
        file_uri = kwargs.get("uri")
        logger.error(
            "Gemini File API does not support downloading binary data back after upload", 
            provider=self.name, 
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
            provider=self.name,
            requested_file=raw_file_name,
            resolved_id=clean_file_id
        )

        try:
            # Gửi request DELETE tới v1beta/files/{clean_file_id}
            response = await self.send(
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
                logger.info("Successfully deleted file from Gemini", provider=self.name, file_id=clean_file_id)
                return True
            elif status_code == 404:
                logger.warning("File already deleted or expired on Gemini", provider=self.name, file_id=clean_file_id)
                return True
                
            return False

        except Exception as e:
            # Nếu hàm self.send tự ném lỗi khi gặp 404, hãy xử lý tại đây để tránh sập luồng vô lý
            if "404" in str(e):
                logger.warning("File not found during delete (might be expired)", provider=self.name, file_id=clean_file_id)
                return True
                
            logger.error("Unexpected error in delete_file", provider=self.name, file_id=clean_file_id, error=str(e))
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

        logger.info("Fetching files list from Gemini File API", provider=self.name)

        try:
            response = await self.send(
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
            return await self.response.adapt_file_list_response(response)

        except Exception as e:
            logger.error("Failed to fetch or parse files list from Gemini API", error=str(e), provider=self.name)
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
            provider=self.name,
            requested_file=raw_file_name,
            resolved_id=clean_file_id
        )

        try:
            # Gửi request GET tới v1beta/files/{clean_file_id}
            response = await self.send(
                client=http_client,
                method="GET",
                api_type=ApiType.FILES,
                model=clean_file_id,  # Tận dụng tham số định tuyến id/name qua model/endpoint
                timeout=timeout
            )

            logger.info("Successfully fetched file metadata from Gemini", provider=self.name, file_id=clean_file_id)
            
            # Sử dụng lại hàm adapt_file_upload_response vì cấu trúc JSON trả về giống nhau
            return await self.response.adapt_file_upload_response(response)

        except Exception as e:
            logger.error("Unexpected error in get_file", provider=self.name, file_id=clean_file_id, error=str(e))
            raise e