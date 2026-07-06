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
    extension: Optional[str]=None
    uri: Optional[str] = None # Đường dẫn file, S3 URI, hoặc URL
    base64_data: Optional[str] = None
    bytes_data: Optional[bytes]=None
    provider_file_id: Optional[str]=None
    source: Literal[
        "local",
        "url",
        "base64",
        "provider",
        "memory"
    ]="local"
    metadata: FileMetadata = Field(default_factory=FileMetadata)

class DocumentContent(GatewayBaseModel):

    attachment: GatewayAttachment

    page_range: Optional[str]=None

    extracted_text: Optional[str]=None

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
    crawl: bool = True
    max_depth: int = 0
    extract_main_content: bool = True
    title: Optional[str] = None

class TextContent(GatewayBaseModel):
    """Nội dung văn bản với nhiều dạng khác nhau."""
    data: str  # raw text
    format: Literal[
        "plain",        # Chuỗi ký tự thuần
        "structured",   # Có định dạng (HTML, Markdown, XML…)
        "code",         # Code snippets
        "dialog",       # Hội thoại / Conversation style
        "creative",     # Văn bản sáng tạo (thơ, truyện…)
        "instructional" # Hướng dẫn / Procedural text
    ] = "plain"
    encoding: Optional[str] = None  # Mã hóa văn bản
    token_count: Optional[int] = None  # Số lượng token
    line_count: Optional[int] = None  # Số lượng dòng
    language: Optional[str] = None  # Ngôn ngữ văn bản

