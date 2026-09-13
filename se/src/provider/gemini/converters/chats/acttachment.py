import base64
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional

from ...file_extension import FileHelper


class BaseAttachmentHandler(ABC):
    """Giao diện chuẩn cho mọi Sub-Adapter xử lý tệp đính kèm."""
    
    @abstractmethod
    def handle(self, part: Dict[str, Any], part_type: str) -> Optional[Dict[str, Any]]:
        """
        Xử lý một phần nội dung đa phương tiện và chuyển đổi thành định dạng inlineData của Gemini.
        Trả về Dictionary cấu trúc phần tử Gemini hoặc None nếu không xử lý được.
        """
        pass

    def _helper_extract_base64_from_uri(self, attachment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Hàm trợ giúp chung cho các handler để xử lý uri/path/data-url nếu không có base64_data sẵn."""
        url_or_path = attachment.get("uri") or attachment.get("url") or attachment.get("path") or ""
        if not url_or_path:
            return None
            
        # Xử lý chuỗi Data URL Base64
        if url_or_path.startswith("data:"):
            header, base64_data = url_or_path.split(";base64,")
            mime_type = header.replace("data:", "")
            return {"mimeType": mime_type, "data": base64_data}
            
        # Xử lý đọc file cục bộ từ đường dẫn hệ thống
        elif Path(url_or_path).is_file():
            file_path = Path(url_or_path)
            # Giả định FileHelper đã được định nghĩa trong hệ thống của bạn
            mime_type = FileHelper.detect_mime_type(file_path)
            with open(file_path, "rb") as f:
                base64_data = base64.b64encode(f.read()).decode('utf-8')
            return {"mimeType": mime_type, "data": base64_data}
            
        return None
    
class MediaContentHandler(BaseAttachmentHandler):
    """Sub-Adapter chuyên trách xử lý các phân tầng Image, Audio, Video."""
    
    def handle(self, part: Dict[str, Any], part_type: str) -> Optional[Dict[str, Any]]:
        media_content_obj = part.get(part_type)
        if not media_content_obj:
            return None
            
        attachment = media_content_obj.get("attachment")
        if not attachment:
            return None
            
        # Kịch bản A: Khách hàng truyền base64 trực tiếp
        if "base64_data" in attachment and attachment.get("base64_data"):
            return {
                "inlineData": {
                    "mimeType": attachment.get("mime_type", "application/octet-stream"),
                    "data": attachment.get("base64_data")
                }
            }
            
        # Kịch bản B: Khách hàng truyền đường dẫn URI/Path/URL web công khai
        extracted = self._helper_extract_base64_from_uri(attachment)
        if extracted:
            return {"inlineData": extracted}
            
        # Fallback nếu là link web public chưa được crawl
        url_or_path = attachment.get("uri") or attachment.get("url") or ""
        if url_or_path and not url_or_path.startswith("data:"):
            return {"text": f"[{part_type.upper()} URL/Path]: {url_or_path}"}
            
        return None
    
class FlatFileHandler(BaseAttachmentHandler):
    """Sub-Adapter chuyên trách xử lý tài liệu đính kèm phẳng không bọc (Trường file)."""
    
    def handle(self, part: Dict[str, Any], part_type: str) -> Optional[Dict[str, Any]]:
        attachment = part.get("file")
        if not attachment:
            return None
            
        if "base64_data" in attachment and attachment.get("base64_data"):
            return {
                "inlineData": {
                    "mimeType": attachment.get("mime_type"),
                    "data": attachment.get("base64_data")
                }
            }
            
        extracted = self._helper_extract_base64_from_uri(attachment)
        if extracted:
            return {"inlineData": extracted}
            
        return None
    
class UrlContextHandler(BaseAttachmentHandler):
    """Sub-Adapter xử lý URL nội dung để model tự crawl."""
    def handle(self, part: Dict[str, Any], part_type: str) -> Optional[Dict[str, Any]]:
        url_data = part.get("url", {})
        return {"text": f"[Link tham khảo]: {url_data.get('url', '')}"}

class OpenAiVisionFallbackHandler(BaseAttachmentHandler):
    """Sub-Adapter duy trì tính tương thích ngược với cấu trúc image_url kiểu OpenAI."""
    def handle(self, part: Dict[str, Any], part_type: str) -> Optional[Dict[str, Any]]:
        img_url = part.get("image_url", {}).get("url", "")
        if img_url.startswith("data:"):
            header, base64_data = img_url.split(";base64,")
            mime_type = header.replace("data:", "")
            return {"inlineData": {"mimeType": mime_type, "data": base64_data}}
        return {"text": f"[Image URL]: {img_url}"}