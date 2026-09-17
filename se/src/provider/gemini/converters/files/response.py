import datetime
from typing import List, Dict, Any, Optional

from .....domain.schemas import (
    GatewayAttachment,
    FileMetadata

)

import structlog
logger = structlog.get_logger(__name__)


class ResponseFiles():
    
    @staticmethod
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

    @staticmethod
    def _response_json(response: Any) -> Dict[str, Any]:
        """Return a JSON object from either an HTTP response or decoded data."""
        raw_data = response if isinstance(response, dict) else response.json()
        if not isinstance(raw_data, dict) or not raw_data:
            raise ValueError("Gemini File API returned an empty or non-object JSON body")
        return raw_data

    def _adapt_file_data(self, file_data: Dict[str, Any]) -> GatewayAttachment:
        if not isinstance(file_data, dict):
            raise TypeError("Gemini file entry must be a JSON object")

        raw_name = file_data.get("name", "")
        file_id = raw_name.removeprefix("files/")
        if not file_id:
            raise ValueError("Gemini file response is missing 'name'")

        size_bytes = file_data.get("sizeBytes")
        try:
            final_size = int(size_bytes) if size_bytes is not None else None
        except (ValueError, TypeError):
            final_size = None

        metadata_dto = FileMetadata(
            checksum_sha256=file_data.get("sha256Hash"),
            created_at=self._parse_iso_to_timestamp(file_data.get("createTime")),
            modified_at=self._parse_iso_to_timestamp(file_data.get("updateTime")),
        )

        return GatewayAttachment(
            id=file_id,
            filename=file_data.get("displayName"),
            mime_type=file_data.get("mimeType", "application/octet-stream"),
            size=final_size,
            uri=file_data.get("uri"),
            source="provider",
            provider_file_id=raw_name,
            metadata=metadata_dto,
        )

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
            raw_data = self._response_json(response)

            # Gemini bọc dữ liệu trong trường "file"
            file_data: Dict[str, Any] = raw_data.get("file", raw_data)

            # 2. Bóc tách và chuẩn hóa thông tin cơ bản
            attachment = self._adapt_file_data(file_data)

            logger.info(
                "Successfully adapted Gemini File API response to GatewayAttachment",
                file_id=attachment.id,
                file_uri=attachment.uri,
            )
            return attachment

        except Exception as e:
            logger.error("Failed to adapt Gemini file upload response due to unexpected error", error=str(e))
            raise e
        
    async def adapt_file_list_response(self, response: Any) -> List[GatewayAttachment]:
        """Chuyển đổi danh sách response từ Gemini File API sang List[GatewayAttachment]."""
        try:
            # Parse JSON từ response

            raw_data = self._response_json(response)
            # Gemini API trả về key 'files' chứa danh sách các file
            gemini_files = raw_data.get("files", [])
            if not isinstance(gemini_files, list):
                raise TypeError("Gemini 'files' field must be an array")
            logger.info("Found raw files in Gemini response, starting mapping", count=len(gemini_files))

            final_attachments: List[GatewayAttachment] = []

            for f in gemini_files:
                try:
                    attachment = self._adapt_file_data(f)
                    final_attachments.append(attachment)
                except Exception as map_err:
                    logger.error(
                        "Error mapping raw Gemini file data to GatewayAttachment", 
                        file_name=f.get("name") if isinstance(f, dict) else None,
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
