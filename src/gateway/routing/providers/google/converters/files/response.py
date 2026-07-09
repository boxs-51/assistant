import datetime
from typing import List, Dict, Any, Optional

from ......schemas import (
    GatewayAttachment,
    FileMetadata

)


import structlog
logger = structlog.get_logger(__name__)


class ResponseFiles():

    def _parse_iso_to_timestamp(iso_str: Optional[str]) -> Optional[int]:
        """Hàm trợ giúp convert ISO datetime string từ Google API sang Unix timestamp."""
        if not iso_str:
            return None
        try:
            # Xử lý ký tự 'Z' của UTC để tương thích với các phiên bản Python cũ/mới
            clean_str = iso_str.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean_str)
            return int(dt.timestamp())
        except Exception:
            return None

    async def adapt_file_upload_response(self, response: Any) -> GatewayAttachment:
        """
        Chuyển đổi response thành công từ bước PUT (Resumable Upload) của Gemini File API 
        sang cấu trúc chuẩn hóa GatewayAttachment DTO.
        """
        try:
            # 1. Kiểm tra mã trạng thái HTTP (Gemini Resumable PUT thường trả về 200 OK hoặc 201 Created)
            status_code = getattr(response, "status_code", None) or getattr(response, "status", 200)
            if status_code not in (200, 201):
                raw_text = ""
                try:
                    raw_text = response.text if hasattr(response, "text") else str(await response.text())
                except Exception:
                    pass
                logger.error("Gemini File API returned failure status code", status_code=status_code, response=raw_text)
                raise ValueError(f"Gemini File API upload failed with status code {status_code}. Response: {raw_text}")

            # Trích xuất dữ liệu JSON từ Response
            # LƯU Ý: Nếu dùng aiohttp, hãy đổi thành: raw_data = await response.json()
            raw_data = response.json()
            if not raw_data:
                logger.error("Gemini File API returned an empty response body")
                raise ValueError("Empty response received from Gemini File API")

            # Gemini bọc dữ liệu trong trường "file"
            file_data: Dict[str, Any] = raw_data.get("file", raw_data)

            # 2. Bóc tách và chuẩn hóa thông tin cơ bản
            raw_name = file_data.get("name", "")  # Cấu trúc trả về thường là: "files/abc123xyz"
            file_id = raw_name.replace("files/", "") if "files/" in raw_name else raw_name
            
            # Xử lý kích thước file (Gemini trả về dạng chuỗi sizeBytes)
            size_bytes = file_data.get("sizeBytes")
            try:
                final_size = int(size_bytes) if size_bytes is not None else None
            except (ValueError, TypeError):
                final_size = None

            # 3. Xử lý timestamps (Chuyển ISO 8601 string thành Unix timestamp int)
            created_timestamp = self._parse_iso_to_timestamp(iso_str=file_data.get("createTime"))
            modified_timestamp = self._parse_iso_to_timestamp(iso_str=file_data.get("updateTime"))

            # 4. Tạo FileMetadata DTO chi tiết
            metadata_dto = FileMetadata(
                checksum_sha256=file_data.get("sha256Hash"),
                created_at=created_timestamp,
                modified_at=modified_timestamp,
                page_count=None,
                language=None,
                encoding=None
            )

            # 5. Khởi tạo và trả về GatewayAttachment hoàn chỉnh
            # Trường 'uri' cực kỳ quan trọng, chính là link 'https://generativelanguage.googleapis.com/...' 
            # để nạp vào cấu trúc fileData sau này.
            file_uri = file_data.get("uri")
            if not file_uri:
                logger.warning("Field 'uri' is missing from Gemini file upload response", file_id=file_id)

            attachment = GatewayAttachment(
                id=file_id,
                filename=file_data.get("displayName"),
                mime_type=file_data.get("mimeType", "application/octet-stream"),
                size=final_size,
                uri=file_uri,
                base64_data=None,  # Đã chuyển lên File API thành công nên trường này luôn để None
                metadata=metadata_dto
            )

            logger.info(
                "Successfully adapted Gemini File API response to GatewayAttachment",
                file_id=file_id,
                file_uri=file_uri
            )
            return attachment

        except Exception as e:
            logger.error("Failed to adapt Gemini file upload response due to unexpected error", error=str(e))
            raise e
        
    async def adapt_file_list_response(self, response: Any) -> List[GatewayAttachment]:
        """Chuyển đổi danh sách response từ Gemini File API sang List[GatewayAttachment]."""
        try:
            # Parse JSON từ response

            raw_data = response.json()
            # Gemini API trả về key 'files' chứa danh sách các file
            gemini_files = raw_data.get("files", [])
            logger.info("Found raw files in Gemini response, starting mapping", count=len(gemini_files))

            final_attachments: List[GatewayAttachment] = []

            for f in gemini_files:
                try:
                    # Giả lập một response bọc độc lập để tái sử dụng hàm adapt_file_upload_response đã viết
                    # Hoặc bạn có thể bóc tách logic parse của hàm đó ra thành một hàm private riêng lẻ
                    mock_file_response = {"file": f}
                    attachment = await self.adapt_file_upload_response(mock_file_response)
                    final_attachments.append(attachment)
                except Exception as map_err:
                    logger.error(
                        "Error mapping raw Gemini file data to GatewayAttachment", 
                        file_name=f.get("name"), 
                        error=str(map_err)
                    )
                    continue

            logger.info(
                "Successfully mapped Gemini files list to Gateway DTOs", 
                raw_count=len(gemini_files), 
                mapped_count=len(final_attachments)
            )
            return final_attachments

        except Exception as e:
            logger.error("Failed to adapt Gemini file list response", error=str(e))
            raise e
