from .base import GatewayBaseModel
from typing import Literal, Optional, Literal
from pydantic import Field
# =================================================================
# 2. ATTACHMENT & CONTENT PARTS (Cấu trúc lõi cho Multimodal)
# =================================================================

class FileMetadata(GatewayBaseModel):
    """Metadata chi tiết cho một tệp đính kèm."""
    page_count: Optional[int] = None
    language: Optional[str] = None
    encoding: Optional[str] = None
    checksum_sha256: Optional[str] = Field(None, alias="sha256")
    created_at: Optional[int] = None
    modified_at: Optional[int] = None

class GatewayAttachment(GatewayBaseModel):
    """
    Cấu trúc tệp đính kèm chung, độc lập với provider.
    Có thể map tới inlineData (Gemini), input_file (OpenAI), document (Claude), etc.
    """
    id: Optional[str] = None
    filename: Optional[str] = None
    mime_type: str
    size: Optional[int] = None
    uri: Optional[str] = None # Đường dẫn file, S3 URI, hoặc URL
    base64_data: Optional[str] = None
    metadata: FileMetadata = Field(default_factory=FileMetadata)

class ImageContent(GatewayBaseModel):
    """Nội dung hình ảnh."""
    attachment: GatewayAttachment
    detail: Literal["auto", "low", "high"] = "auto"

class AudioContent(GatewayBaseModel):
    """Nội dung âm thanh."""
    attachment: GatewayAttachment
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    duration_seconds: Optional[float] = None

class VideoContent(GatewayBaseModel):
    """Nội dung video."""
    attachment: GatewayAttachment
    duration_seconds: Optional[float] = None

class UrlContent(GatewayBaseModel):
    """Nội dung từ một URL để model tự crawl (URL Context)."""
    url: str
    title: Optional[str] = None

