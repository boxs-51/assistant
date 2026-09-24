from .base import GatewayBaseModel
from typing import Literal, Optional, Literal
from pydantic import Field, model_validator
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
    asset_id: Optional[str] = None
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
        "memory",
        "asset",
    ]="local"
    metadata: FileMetadata = Field(default_factory=FileMetadata)

    @model_validator(mode="before")
    @classmethod
    def _validate_canonical_asset_shape(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        asset_id = data.get("asset_id")
        source = data.get("source")
        if asset_id:
            if source not in (None, "asset", ""):
                raise ValueError(
                    "asset_id requires source='asset'; legacy/provider identities "
                    "must not be mixed with canonical assets."
                )
            expected_uri = f"asset://{asset_id}"
            uri = data.get("uri")
            if uri not in (None, "", expected_uri):
                raise ValueError(
                    "Canonical asset uri must match asset://<asset_id>."
                )
            for field in ("base64_data", "bytes_data", "provider_file_id"):
                if data.get(field) not in (None, ""):
                    raise ValueError(
                        f"Canonical asset attachment cannot carry {field}."
                    )
            data["source"] = "asset"
            data["uri"] = expected_uri
            return data
        if source == "asset":
            raise ValueError("source='asset' requires asset_id.")
        return data

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
